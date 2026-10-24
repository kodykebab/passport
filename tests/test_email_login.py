"""Email login input boundaries and issuer policy checks."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport import email_login as email
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest


class EmailBoundaryTests(unittest.TestCase):
	def tearDown(self):
		for key in ("request", "form_dict", "db"):
			setattr(frappe.local, key, None)

	def test_safe_return_destinations(self):
		for path in (
			None,
			"https://elsewhere.test/account",
			"//elsewhere.test/account",
			"//[invalid",
			"/\\elsewhere.test",
			"/desk",
			"/account\n",
			"/account/../login",
		):
			with self.subTest(path=path):
				self.assertEqual(email.return_path(path), "/account")
		path = "/oidc/authorize?client_id=site1.localhost&state=example"
		self.assertEqual(email.return_path(path), path)

	def test_invalid_request_shapes(self):
		for changes in (
			{"content_length": 4097},
			{"is_json": True},
			{"args": MultiDict([("attempt", "a"), ("attempt", "b")])},
		):
			with self.subTest(changes=changes):
				values = dict(
					path=email.PATH, content_length=0, is_json=False, args=MultiDict(), form=MultiDict()
				)
				values.update(changes)
				frappe.local.request = SimpleNamespace(**values)
				frappe.local.form_dict = frappe._dict()
				with self.assertRaises(BadRequest):
					email.boundary()
		for key in ("sid", "cmd"):
			frappe.local.request = SimpleNamespace(path=email.PATH, content_length=0, is_json=False)
			frappe.local.form_dict = frappe._dict({key: "override"})
			with self.assertRaises(BadRequest):
				email.boundary()

	def test_email_login_never_bypasses_mfa_or_disabled_user(self):
		frappe.local.db = Mock()
		with patch.object(frappe, "get_system_settings", return_value=True):
			self.assertFalse(email.lock_and_check_user("alice@example.com", "alice@example.com"))
			frappe.db.sql.assert_not_called()
		with patch.object(frappe, "get_system_settings", return_value=False):
			for rows in ([], [(0, "alice@example.com")], [(1, "changed@example.com")]):
				frappe.db.sql.return_value = rows
				self.assertFalse(email.lock_and_check_user("alice@example.com", "alice@example.com"))
			for user in (None, "Guest", "Administrator"):
				self.assertFalse(email.lock_and_check_user(user, "alice@example.com"))
