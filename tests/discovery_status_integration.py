import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import frappe
from passport_client import discovery, store


class DiscoveryStatusTests(unittest.TestCase):
	def test_status_counts_only_current_pending_work_and_reports_configuration(self):
		installation = uuid.uuid4().hex
		with patch.dict(
			frappe.conf,
			{
				"passport_installation": installation,
				"passport_disabled": True,
				"passport_enrollment": None,
				"pause_scheduler": True,
			},
		):
			store.put("admission_policy", "site", {"mode": "enforced", "installation": installation})
			for index, (revision, pending, retry_at) in enumerate(
				[
					(installation, True, 0),
					(installation, True, 100),
					(installation, False, 0),
					("superseded", True, 100),
				]
			):
				store.put(
					"discovery",
					installation + str(index),
					{"installation": revision, "pending": pending, "retry_at": retry_at},
				)
			status = discovery.get_status()
			self.assertEqual(
				status,
				{
					"enrolled": True,
					"suspended": True,
					"scheduler_enabled": False,
					"pending": 2,
					"retrying": 1,
				},
			)
			self.assertNotIn(installation, json.dumps(status))
			frappe.conf.passport_installation = "unapproved"
			self.assertFalse(discovery.get_status()["enrolled"])

	def test_status_requires_manager(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				discovery.get_status()
		finally:
			frappe.set_user("Administrator")

	def tearDown(self):
		frappe.db.rollback()


if __name__ == "__main__":
	sites = Path("/tmp/passport-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	frappe.init(site="test.local", sites_path=str(sites))
	frappe.connect()
	try:
		frappe.set_user("Administrator")
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(DiscoveryStatusTests)
		)
		raise SystemExit(not result.wasSuccessful())
	finally:
		frappe.db.rollback()
		frappe.destroy()
