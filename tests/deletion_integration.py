"""Native issuer deletion and retries on disposable users; no email or demo mutation."""

import secrets
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import requests
from frappe.utils.password import check_password
from live_support import ID, db
from passport import deletion, oidc, store


class DeletionIntegrationTests(unittest.TestCase):
	def setUp(self):
		self.user = "delete-test-" + secrets.token_hex(10) + "@example.invalid"
		self.password = secrets.token_urlsafe(32)
		self.subjects = []
		self.addCleanup(self.cleanup)
		self.create_user()

	def create_user(self):
		with db("id.localhost"), patch.object(frappe, "enqueue"):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": self.user,
					"first_name": "Deletion Fixture",
					"user_type": "Website User",
					"new_password": self.password,
					"send_welcome_email": 0,
				}
			)
			user.flags.no_welcome_mail = True
			user.insert(ignore_permissions=True)
			account = frappe.get_doc({"doctype": "Passport Account", "user": self.user}).insert(
				ignore_permissions=True
			)
			self.subjects.append(account.name)

	def cleanup(self):
		with db("id.localhost"):
			for subject in self.subjects:
				if frappe.db.exists("Passport Account", subject):
					frappe.delete_doc(
						"Passport Account",
						subject,
						ignore_permissions=True,
						force=True,
						delete_permanently=True,
					)
				if not (store.get("account_deletion", subject) or {}).get("deleted"):
					frappe.db.sql(
						"DELETE FROM passport_state WHERE kind='account_deletion' AND id=%s", subject
					)
			if frappe.db.exists("User", self.user):
				frappe.delete_doc(
					"User", self.user, ignore_permissions=True, force=True, delete_permanently=True
				)
			frappe.db.sql("DELETE FROM passport_state WHERE user=%s", self.user)
			for name in frappe.get_all("Contact", filters={"email_id": self.user}, pluck="name"):
				frappe.delete_doc(
					"Contact", name, ignore_permissions=True, force=True, delete_permanently=True
				)

	def test_native_deletion_reuse_gets_new_subject_and_no_old_state(self):
		with db("id.localhost"):
			frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", self.user)
			store.put(
				"connection",
				"fixture-" + self.subjects[0],
				{"user": self.user, "client": "fixture", "revoked": True, "generation": 1},
			)
			self.assertTrue(deletion.request(self.user))
			self.assertFalse(frappe.db.exists("User", self.user))
			self.assertFalse(frappe.db.exists("Passport Account", self.subjects[0]))
			self.assertEqual(store.get("account_deletion", self.subjects[0]), {"deleted": True})
			self.assertFalse(store.rows("connection", user=self.user))
		self.create_user()
		with db("id.localhost"):
			self.assertNotEqual(self.subjects[0], self.subjects[1])
			self.assertEqual(check_password(self.user, self.password), self.user)
			self.assertTrue(deletion.finish(self.subjects[0]))
			self.assertTrue(frappe.db.exists("User", self.user))

	def test_native_failure_is_disabled_then_retry_finishes(self):
		with db("id.localhost"):
			with (
				patch.object(frappe, "delete_doc", side_effect=frappe.LinkExistsError("fixture blocker")),
				patch.object(frappe, "log_error"),
			):
				self.assertFalse(deletion.request(self.user))
		with db("id.localhost"):
			self.assertFalse(frappe.db.get_value("User", self.user, "enabled"))
			self.assertTrue(deletion.pending(self.user))
			self.assertTrue(deletion.finish(self.subjects[0]))
			self.assertFalse(frappe.db.exists("User", self.user))

	def test_retry_and_renewal_during_native_session_commit(self):
		with requests.Session() as browser:
			response = browser.post(ID + "/api/method/login", data={"usr": self.user, "pwd": self.password})
			self.assertTrue(response.ok)
			sid = browser.cookies.get("sid", domain="id.localhost")
		login = "deletion-race-" + secrets.token_hex(12)
		body = {"sid": login, "binding": login, "operation": login, "deadline": int(time.time()) + 3600}
		client = SimpleNamespace(name="deletion-fixture")
		with db("id.localhost"):
			store.create(
				"login",
				login,
				{
					"client": client.name,
					"user": self.user,
					"sub": self.subjects[0],
					"issuer_sid": sid,
					"revoked": False,
					"deadline": body["deadline"],
					"pending_until": time.time() + 120,
					"connection_generation": 0,
				},
			)
			frappe.local.request = SimpleNamespace(path="/passport/complete")
			self.assertIn("lease", oidc.lease(client, body))

		committed, resume = threading.Event(), threading.Event()
		native_delete = deletion.delete_session

		def pause_after_commit(*args, **kwargs):
			native_delete(*args, **kwargs)
			committed.set()
			if not resume.wait(20):
				raise TimeoutError("Concurrent retry did not complete")

		def remove():
			with db("id.localhost"):
				frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", self.user)
				return deletion.request(self.user)

		with (
			patch.object(deletion, "delete_session", side_effect=pause_after_commit),
			ThreadPoolExecutor(1) as pool,
		):
			first = pool.submit(remove)
			try:
				self.assertTrue(committed.wait(20), "Native session deletion must commit")
				with db("id.localhost"):
					self.assertFalse(frappe.db.get_value("User", self.user, "enabled"))
					with self.assertRaisesRegex(ValueError, "login_revoked"):
						oidc.lease(client, body)
				with db("id.localhost"):
					self.assertTrue(deletion.finish(self.subjects[0]))
			finally:
				resume.set()
			self.assertTrue(first.result(timeout=20))
		with db("id.localhost"):
			self.assertEqual(store.get("account_deletion", self.subjects[0]), {"deleted": True})
			self.assertFalse(frappe.db.exists("User", self.user))
			self.assertFalse(store.get("login", login))
			self.assertTrue(deletion.finish(self.subjects[0]))


if __name__ == "__main__":
	unittest.main(verbosity=2)
