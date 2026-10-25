"""Build against the existing bench with isolated cache and asset-retention canaries."""

import hashlib
import secrets
import subprocess
import time

import frappe
from live_support import ROOT, db

nonce = secrets.token_hex(16)
fixtures = []
keys = []
configs = {}
started = time.monotonic()
try:
	for site in ("id.localhost", "site1.localhost"):
		path = ROOT / ".bench/sites" / site / "site_config.json"
		configs[path] = hashlib.sha256(path.read_bytes()).digest()
	with db("id.localhost"):
		for prefix in ("passport:email-limit:", "passport-deployment-test:"):
			key = frappe.cache.make_key(prefix + nonce)
			keys.append(key)
			frappe.cache.set(key, 3, ex=3600)
	for app in ("passport", "passport_client"):
		path = ROOT / f"apps/{app}/{app}/public/ui/passport.retention-{nonce}.js"
		path.parent.mkdir(parents=True, exist_ok=True)
		fixtures.append(path)
		path.write_text("export const retained = true;\n")
	path = ROOT / f"apps/passport_client/passport_client/public/dist/js/passport_desk.bundle.{nonce}.js"
	path.parent.mkdir(parents=True, exist_ok=True)
	fixtures.append(path)
	path.write_text("// Previously served asset retention fixture.\n")
	subprocess.run(
		[str(ROOT / ".bench/env/bin/python"), str(ROOT / "env/deployment.py"), "build"],
		cwd=ROOT,
		check=True,
	)
	with db("id.localhost"):
		for key in keys:
			assert frappe.cache.get(key) == b"3", "Build removed a cache canary"
			ttl = frappe.cache.ttl(key)
			elapsed = time.monotonic() - started
			assert 3595 - elapsed <= ttl <= 3601 - elapsed, "Build changed counter expiry"
	for path, digest in configs.items():
		assert hashlib.sha256(path.read_bytes()).digest() == digest, "Build changed site configuration"
	for path in fixtures:
		assert path.is_file(), "Build deleted a previously served asset"
	print("PASS build preserves counter values/expiry, unrelated cache and previous UI/Desk assets")
finally:
	try:
		if keys:
			with db("id.localhost"):
				frappe.cache.delete(*keys)
	finally:
		for path in fixtures:
			path.unlink(missing_ok=True)
