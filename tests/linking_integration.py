"""Real SQL conflicts for explicit linking; no public policy switch or mail."""

import secrets
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import frappe
from live_support import db
from passport_client import auth, linking


class LinkingIntegrationTests(unittest.TestCase):
	def setUp(self):
		self.users = ["link-sql-" + secrets.token_hex(10) + "@example.invalid" for _ in range(2)]
		with db("site1.localhost"), patch.object(frappe, "enqueue"):
			for email in self.users:
				user = frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": "Site Name",
						"enabled": 1,
						"user_type": "Website User",
						"send_welcome_email": 0,
					}
				)
				user.flags.no_welcome_mail = True
				user.insert(ignore_permissions=True)
		self.config = patch.object(
			auth, "config", return_value=frappe._dict(issuer="http://id.localhost:8080")
		)
		self.config.start()
		self.addCleanup(self.config.stop)
		self.session = patch.object(linking, "require_local_user", side_effect=self.lock_user)
		self.session.start()
		self.addCleanup(self.session.stop)

	@staticmethod
	def lock_user(*, lock=False):
		assert lock
		frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", frappe.session.user)
		return frappe.session.user

	def tearDown(self):
		with db("site1.localhost"):
			for email in self.users:
				for name in frappe.get_all("Passport Identity", filters={"user": email}, pluck="name"):
					frappe.delete_doc("Passport Identity", name, ignore_permissions=True, force=True)
				frappe.delete_doc("User", email, ignore_permissions=True, force=True)
				frappe.db.sql("DELETE FROM passport_state WHERE user=%s", email)
				frappe.db.delete("Deleted Document", {"deleted_name": email})

	def finish(self, user, subject):
		with db("site1.localhost"):
			frappe.local.session = frappe._dict(user=user, sid="fixture")
			try:
				linking.finish(
					{"user": user, "local_sid": "fixture", "generation": 0, "exp": time.time() + 600},
					{"sub": subject},
				)
				return "linked"
			except auth.PassportError:
				frappe.db.rollback()
				return "rejected"

	def test_same_subject_cannot_link_to_two_users(self):
		subject = secrets.token_hex(20)
		with ThreadPoolExecutor(max_workers=2) as pool:
			results = list(pool.map(lambda user: self.finish(user, subject), self.users))
		self.assertCountEqual(results, ["linked", "rejected"])
		with db("site1.localhost"):
			self.assertEqual(frappe.db.count("Passport Identity", {"subject": subject}), 1)

	def test_same_user_cannot_receive_two_subjects(self):
		with ThreadPoolExecutor(max_workers=2) as pool:
			results = list(
				pool.map(
					lambda subject: self.finish(self.users[0], subject),
					[secrets.token_hex(20), secrets.token_hex(20)],
				)
			)
		self.assertCountEqual(results, ["linked", "rejected"])
		with db("site1.localhost"):
			self.assertEqual(frappe.db.count("Passport Identity", {"user": self.users[0]}), 1)
			self.assertEqual(frappe.db.get_value("User", self.users[0], "full_name"), "Site Name")


if __name__ == "__main__":
	unittest.main(verbosity=2)
