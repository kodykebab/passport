"""Deployment lock and asset rollback boundaries, without rebuilding the live environment."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from filelock import FileLock, Timeout

from env import deployment


class DeploymentTests(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.root = Path(self.directory.name)
		self.bench = self.root / ".bench"
		(self.bench / "config").mkdir(parents=True)
		self.files = [
			self.root / f"apps/{app}/{app}/public/ui/manifest.json" for app in ("passport", "passport_client")
		]
		self.files += [
			self.root / "apps/passport_client/passport_client/public/dist/js/old.js",
			self.bench / "sites/assets/assets.json",
			self.bench / "sites/assets/assets-rtl.json",
		]
		for path in self.files:
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_text("previous")
		self.root_patch = patch.object(deployment, "ROOT", self.root)
		self.root_patch.start()
		self.addCleanup(self.root_patch.stop)

	def assert_restored(self):
		for path in self.files:
			self.assertEqual(path.read_text(), "previous")
		self.assertFalse(self.files[2].with_name("new.js").exists())

	def partial_build(self, bench):
		for path in self.files:
			path.write_text("new")
		self.files[2].unlink()
		self.files[2].with_name("new.js").write_text("new bundle")

	def test_failure_restores_assets_and_manifests(self):
		for stage in ("compile", "cache", "validation"):
			with self.subTest(stage=stage):

				def compile_assets(bench):
					self.partial_build(bench)
					if stage == "compile":
						raise RuntimeError("compile failed")

				with (
					patch.object(deployment, "compile_assets", side_effect=compile_assets),
					patch.object(deployment, "refresh_asset_cache") as refresh,
					patch.object(deployment, "check_site") as check,
				):
					if stage == "cache":
						refresh.side_effect = [RuntimeError("cache failed"), None]
					if stage == "validation":
						check.side_effect = RuntimeError("validation failed")
					with self.assertRaises(RuntimeError):
						deployment.build(self.bench, [("issuer", "passport")])
					self.assert_restored()
					refresh.assert_called_with(self.bench, [("issuer", "passport")])

	def test_success_keeps_new_manifests_and_previous_bundles(self):
		with (
			patch.object(deployment, "compile_assets", side_effect=self.partial_build),
			patch.object(deployment, "refresh_asset_cache"),
			patch.object(deployment, "check_site"),
		):
			deployment.build(self.bench, [("issuer", "passport")])
		self.assertEqual(self.files[0].read_text(), "new")
		self.assertEqual(self.files[3].read_text(), "new")
		self.assertEqual(self.files[2].read_text(), "previous")
		self.assertTrue(self.files[2].with_name("new.js").exists())

	def test_native_build_lock_prevents_any_build_work(self):
		with (
			FileLock(self.bench / "config/bench_build.lock"),
			patch.object(deployment, "FileLock", side_effect=lambda path, timeout: FileLock(path, timeout=0)),
			patch.object(deployment, "compile_assets") as compile_assets,
			patch.object(deployment, "refresh_asset_cache") as refresh,
		):
			with self.assertRaises(Timeout):
				deployment.build(self.bench, [("issuer", "passport")])
			compile_assets.assert_not_called()
			refresh.assert_not_called()
		self.assert_restored()

	def test_failed_first_build_removes_new_assets(self):
		new_directory = self.root / "new-assets"
		with self.assertRaises(RuntimeError):
			with deployment.preserve_assets([new_directory]):
				new_directory.mkdir()
				(new_directory / "manifest.json").write_text("new")
				raise RuntimeError("failed")
		self.assertFalse(new_directory.exists())


class NodeVersionTests(unittest.TestCase):
	def test_supported_build_runtime_versions(self):
		for version in ("v20.19.0", "v20.20.0", "v22.12.0", "v24.0.0", "v26.0.0"):
			with (
				self.subTest(version=version),
				patch.object(deployment.subprocess, "check_output", return_value=version),
			):
				deployment.check_node_version()

	def test_unsupported_runtime_fails_before_build(self):
		for version in ("v18.20.0", "v20.18.9", "v21.7.0", "v22.11.0"):
			with (
				self.subTest(version=version),
				patch.object(deployment.subprocess, "check_output", return_value=version),
			):
				with self.assertRaisesRegex(RuntimeError, "requires Node"):
					deployment.check_node_version()
