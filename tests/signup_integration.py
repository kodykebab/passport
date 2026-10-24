"""Isolated signup SQL tests. Public signup stays disabled; no mail is sent."""

import secrets
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from live_support import db
from passport import email_login, signup, store
from passport_client.auth import PassportError, resolve_user


class SignupIntegrationTests(unittest.TestCase):
	def setUp(self):
		self.address = "signup-test-" + secrets.token_hex(10) + "@example.invalid"
		self.password = secrets.token_urlsafe(32)
		self.attempts = []
		self.policy = patch.object(signup, "enabled", return_value=True)
		self.policy.start()
		self.addCleanup(self.policy.stop)
		self.jobs = patch.object(frappe, "enqueue")
		self.jobs.start()
		self.addCleanup(self.jobs.stop)

	def tearDown(self):
		with db("id.localhost"):
			for attempt in self.attempts:
				frappe.db.sql("DELETE FROM passport_state WHERE kind='email_challenge' AND id=%s", attempt)
			for account in frappe.get_all("Passport Account", filters={"user": self.address}, pluck="name"):
				frappe.delete_doc("Passport Account", account, ignore_permissions=True, force=True)
			if frappe.db.exists("User", self.address):
				frappe.delete_doc("User", self.address, ignore_permissions=True, force=True)
			frappe.db.sql("DELETE FROM passport_state WHERE kind='user_generation' AND id=%s", self.address)

	def challenge(self, **changes):
		attempt = secrets.token_hex(16)
		self.attempts.append(attempt)
		with db("id.localhost"):
			row = {
				"purpose": "signup",
				"user": None,
				"email": self.address,
				"browser": "fixture-browser",
				"user_generation": None,
				"return": "/account",
				"deadline": int(time.time()) + 900,
				"expires": int(time.time()) + 300,
				"resend_after": 0,
				"failures": 0,
				"spent": False,
				"generation": 1,
				"mail": [],
				"digest": email_login.keyed_digest(attempt, 1, "123456"),
				**changes,
			}
			store.create("email_challenge", attempt, row)
		return attempt

	def verify(self, attempt, code="123456"):
		with db("id.localhost"):
			frappe.local.session = frappe._dict(user="Guest", sid="Guest")
			frappe.local.login_manager = SimpleNamespace(login_as=Mock())
			try:
				return email_login.verify(
					attempt,
					"fixture-browser",
					code,
					"1",
					purpose="signup",
					display_name="Signup Test",
					password=self.password,
				)
			except Exception:
				frappe.db.rollback()
				raise

	def test_invalid_or_expired_code_creates_nothing(self):
		for changes, code in (({}, "999999"), ({"expires": 0}, "123456")):
			with self.assertRaises(email_login.ChallengeError):
				self.verify(self.challenge(**changes), code)
		with db("id.localhost"):
			self.assertFalse(frappe.db.exists("User", self.address))

	def test_verified_account_password_and_client_non_admission(self):
		attempt = self.challenge()
		self.assertEqual(self.verify(attempt), "/account")
		with db("id.localhost"):
			from frappe.utils.password import check_password

			user = frappe.get_doc("User", self.address)
			self.assertEqual(user.user_type, "Website User")
			self.assertFalse(user.roles)
			self.assertEqual(
				check_password(self.address, self.password, delete_tracker_cache=False), self.address
			)
			self.assertEqual(frappe.db.count("Passport Account", {"user": self.address}), 1)
			subject = frappe.db.get_value("Passport Account", {"user": self.address}, "name")
			row = store.get("email_challenge", attempt)
			self.assertTrue(row["spent"])
			self.assertNotIn(self.password, str(row))
		with self.assertRaises(email_login.ChallengeError):
			self.verify(attempt)
		with db("site1.localhost"):
			self.assertFalse(frappe.db.exists("User", self.address))
			self.assertFalse(frappe.db.exists("Passport Identity", {"subject": subject}))
			with self.assertRaises(PassportError):
				resolve_user("http://id.localhost:8080", {"sub": subject})

	def test_concurrent_verified_attempts_create_only_one_account(self):
		attempts = [self.challenge(), self.challenge()]

		def consume(attempt):
			try:
				return self.verify(attempt)
			except email_login.ChallengeError, frappe.DuplicateEntryError:
				return "rejected"

		with ThreadPoolExecutor(max_workers=2) as pool:
			results = list(pool.map(consume, attempts))
		self.assertCountEqual(results, ["/account", "rejected"])
		with db("id.localhost"):
			self.assertEqual(frappe.db.count("User", {"email": self.address}), 1)
			self.assertEqual(frappe.db.count("Passport Account", {"user": self.address}), 1)

	def test_existing_account_cannot_be_replaced(self):
		self.verify(self.challenge())
		with db("id.localhost"):
			before = frappe.db.get_value("User", self.address, ["full_name", "enabled", "user_type"])
		with self.assertRaises(email_login.ChallengeError):
			self.verify(self.challenge())
		with db("id.localhost"):
			self.assertEqual(
				frappe.db.get_value("User", self.address, ["full_name", "enabled", "user_type"]), before
			)

	def test_partial_creation_rolls_back_but_code_stays_spent(self):
		attempt = self.challenge()
		original = frappe.get_doc

		def fail_account(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == "Passport Account":
				raise RuntimeError("fixture creation failure")
			return original(*args, **kwargs)

		with patch.object(frappe, "get_doc", side_effect=fail_account):
			with self.assertRaisesRegex(RuntimeError, "fixture creation failure"):
				self.verify(attempt)
		with db("id.localhost"):
			self.assertFalse(frappe.db.exists("User", self.address))
			self.assertTrue(store.get("email_challenge", attempt)["spent"])


if __name__ == "__main__":
	unittest.main(verbosity=2)
