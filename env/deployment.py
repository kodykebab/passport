"""Check and build Passport in the retained bench without flushing authentication state."""

import argparse
import importlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "4dc04fac4dd5bf20feb9f24e029202b3eadb52b8"


def require(condition, message):
	if not condition:
		raise RuntimeError(message)


def check_node_version():
	version = subprocess.check_output(["node", "--version"], text=True).strip()
	parts = tuple(int(part) for part in version.removeprefix("v").split("."))
	require(
		len(parts) == 3 and ((parts[0] == 20 and parts >= (20, 19, 0)) or parts >= (22, 12, 0)),
		"Frontend requires Node 20.19+ within v20, or Node 22.12+",
	)


def preflight(bench):
	head = subprocess.check_output(
		["git", "-C", str(bench / "apps/frappe"), "rev-parse", "HEAD"], text=True
	).strip()
	require(head == BASELINE, "Unexpected Frappe baseline; do not upgrade the retained checkout")
	subprocess.run(
		[
			"git",
			"-C",
			str(bench / "apps/frappe"),
			"apply",
			"--reverse",
			"--check",
			str(ROOT / "patches/frappe-development-session-integration.patch"),
		],
		check=True,
	)
	import frappe

	require(
		Path(frappe.__file__).resolve().parent == bench / "apps/frappe/frappe",
		"Frappe is not installed from the retained bench checkout",
	)
	for app in ("passport", "passport_client", "passport_core"):
		module = importlib.import_module(app)
		require(
			Path(module.__file__).resolve().parent == ROOT / "apps" / app / app,
			f"{app} is not installed from this source checkout",
		)
	for tool in ("node", "npm", "yarn"):
		require(shutil.which(tool), f"Missing build tool: {tool}")
	check_node_version()


def check_site(bench, site, app, check_assets=True):
	import frappe
	from frappe.email.doctype.email_account.email_account import EmailAccount
	from frappe.utils.background_jobs import get_queue, get_workers

	require((bench / "sites" / site / "site_config.json").is_file(), f"Missing existing site: {site}")
	frappe.init(site=site, sites_path=str(bench / "sites"))
	frappe.connect()
	try:
		require(app in frappe.get_installed_apps(), f"{site}: {app} is not installed")
		require(frappe.cache.ping(), f"{site}: Redis unavailable")
		require("passport_state" in frappe.db.get_tables(cached=False), f"{site}: Passport schema missing")
		require(frappe.conf.host_name, f"{site}: host_name missing")
		if app == "passport":
			for key in (
				"passport_issuer",
				"passport_signing_key",
				"passport_signing_kid",
				"passport_email_code_key",
			):
				require(frappe.conf.get(key), f"{site}: {key} missing; provision explicitly")
			require(
				"passport:email-limit:" in frappe.get_hooks("persistent_cache_keys"),
				f"{site}: email limit persistence hook missing",
			)
			require(
				frappe.get_hooks("override_email_send")
				or EmailAccount.find_outgoing(match_by_email=frappe.conf.get("passport_email_sender")),
				f"{site}: configure the native outgoing email service",
			)
			require(get_workers(get_queue("default")), f"{site}: no default-queue worker registered")
		else:
			for key in ("passport_issuer", "passport_client_id", "passport_private_key", "passport_key_id"):
				require(frappe.conf.get(key), f"{site}: {key} missing; provision explicitly")
			require(
				frappe.get_system_settings("setup_complete"), f"{site}: finish native setup as Administrator"
			)
		if check_assets:
			directory = Path(frappe.get_app_path(app, "public", "ui"))
			entry = json.loads((directory / "manifest.json").read_text())["src/main.js"]
			for name in [entry["file"], *entry["css"]]:
				require((directory / name).is_file(), f"{site}: missing built UI asset")
			if app == "passport_client":
				manifest = json.loads((bench / "sites/assets/assets.json").read_text())
				bundle = manifest.get("passport_desk.bundle.js", "")
				require(bundle.startswith("/assets/passport_client/"), f"{site}: client bundle missing")
				require(
					(bench / "sites" / bundle.lstrip("/")).is_file(), f"{site}: client bundle file missing"
				)
		print(f"OK {site}: configuration, services and {'assets' if check_assets else 'build prerequisites'}")
	finally:
		frappe.db.rollback()
		frappe.destroy()


@contextmanager
def preserve_assets(paths):
	with tempfile.TemporaryDirectory(prefix="passport-assets-") as directory:
		backups = {}
		for index, path in enumerate(paths):
			backup = Path(directory) / str(index)
			if path.is_dir():
				shutil.copytree(path, backup)
			elif path.exists():
				shutil.copy2(path, backup)
			backups[path] = backup
		try:
			yield
			# Native bundling removes old hashes; keep them available to existing pages.
			for path, backup in backups.items():
				if not backup.is_dir():
					continue
				for old in backup.rglob("*"):
					target = path / old.relative_to(backup)
					if old.is_file() and not target.exists():
						target.parent.mkdir(parents=True, exist_ok=True)
						shutil.copy2(old, target)
		except BaseException:
			for path, backup in backups.items():
				if path.is_dir():
					shutil.rmtree(path)
				else:
					path.unlink(missing_ok=True)
				if backup.is_dir():
					shutil.copytree(backup, path)
				elif backup.exists():
					path.parent.mkdir(parents=True, exist_ok=True)
					shutil.copy2(backup, path)
			raise


def refresh_asset_cache(bench, sites):
	import frappe

	for site, _ in sites:
		frappe.init(site=site, sites_path=str(bench / "sites"))
		try:
			frappe.client_cache.delete_value("assets_json", shared=True)
		finally:
			frappe.destroy()


def compile_assets(bench):
	import frappe
	from frappe.build import get_node_env, make_asset_dirs, setup

	subprocess.run(["npm", "ci", "--prefix", str(ROOT / "frontend")], check=True)
	subprocess.run(["npm", "run", "build", "--prefix", str(ROOT / "frontend")], check=True)
	frappe.init("", sites_path=str(bench / "sites"))
	try:
		setup()
		make_asset_dirs()
		# Native bench build flushes Redis. Invoke its bundler without that final step.
		subprocess.run(
			["yarn", "run", "production", "--apps", "passport_client", "--run-build-command"],
			cwd=bench / "apps/frappe",
			env={**os.environ, **get_node_env()},
			check=True,
		)
	finally:
		frappe.destroy()


def build(bench, sites):
	paths = [ROOT / f"apps/{app}/{app}/public/ui" for app in ("passport", "passport_client")]
	paths += [ROOT / "apps/passport_client/passport_client/public/dist"]
	paths += [bench / "sites/assets" / name for name in ("assets.json", "assets-rtl.json")]
	# Use the same filesystem lock as native bench build, including npm installation.
	with FileLock(bench / "config/bench_build.lock", timeout=10):
		try:
			with preserve_assets(paths):
				compile_assets(bench)
				refresh_asset_cache(bench, sites)
				for site, app in sites:
					check_site(bench, site, app)
		except BaseException:
			refresh_asset_cache(bench, sites)
			raise


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("command", choices=("check", "build"))
	parser.add_argument("--bench", type=Path, default=ROOT / ".bench")
	parser.add_argument("--issuer", default="id.localhost")
	parser.add_argument("--client", default="site1.localhost")
	args = parser.parse_args()
	bench = args.bench.resolve()
	os.chdir(bench / "sites")
	preflight(bench)
	sites = [(args.issuer, "passport"), (args.client, "passport_client")]
	for site, app in sites:
		check_site(bench, site, app, check_assets=args.command == "check")
	if args.command == "build":
		build(bench, sites)


if __name__ == "__main__":
	main()
