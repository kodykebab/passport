"""Signup policy, purpose binding and credential boundaries."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport import email_login, signup


class SignupTests(unittest.TestCase):
	def setUp(self):
		frappe.local.conf = frappe._dict(passport_allow_signup=1, passport_email_code_key="test")
		frappe.local.db = Mock()

	def tearDown(self):
		for key in ("conf", "db", "request", "session"):
			setattr(frappe.local, key, None)

	def test_explicit_opt_in_and_native_registration_restrictions(self):
		with (
			patch.object(frappe, "get_website_settings", return_value=True) as website,
			patch.object(frappe, "get_system_settings", return_value=False) as settings,
		):
			self.assertTrue(signup.enabled())
			frappe.conf.passport_allow_signup = 0
			self.assertFalse(signup.enabled())
			frappe.conf.passport_allow_signup = 1
			website.return_value = False
			self.assertFalse(signup.enabled())
			website.return_value = True
			settings.return_value = True
			self.assertFalse(signup.enabled())

	def test_disabled_route_does_not_issue_challenges(self):
		frappe.local.request = SimpleNamespace(path=signup.PATH)
		with (
			patch.object(signup, "enabled", return_value=False),
			patch.object(email_login, "render_page", return_value="unavailable"),
			patch.object(email_login, "issue") as issue,
		):
			self.assertEqual(email_login.response().status_code, 403)
			issue.assert_not_called()

	def test_challenge_purposes_cannot_be_swapped(self):
		for stored, requested in (("signup", "login"), ("login", "signup")):
			with patch.object(email_login.store, "get", return_value={"purpose": stored, "browser": "proof"}):
				with self.assertRaises(email_login.ChallengeError):
					email_login.lock_challenge("attempt", "proof", requested)
		frappe.db.commit.assert_not_called()

	def test_profile_uses_native_password_policy(self):
		with patch.object(signup, "test_password_strength", return_value={}) as policy:
			self.assertEqual(signup.validate_profile("new@example.test", " Name ", "password"), "Name")
			policy.assert_called_once_with("password", user_data=("Name", "new@example.test"))
			for name, password in (("", "password"), ("Name", ""), ("Name", "x" * 513)):
				with self.assertRaises(email_login.ChallengeError):
					signup.validate_profile("new@example.test", name, password)
			policy.return_value = {"feedback": {"password_policy_validation_passed": False}}
			with self.assertRaisesRegex(email_login.ChallengeError, "stronger"):
				signup.validate_profile("new@example.test", "Name", "weak")

	def test_existing_user_is_never_modified(self):
		with (
			patch.object(signup, "enabled", return_value=True),
			patch.object(frappe, "get_doc") as get_doc,
		):
			frappe.db.exists.return_value = True
			with self.assertRaises(email_login.ChallengeError):
				signup.create_verified_user("alice@example.com", "changed", "changed")
			get_doc.assert_not_called()
