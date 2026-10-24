"""Focused regressions for account reconfirmation and terminal-session recovery."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport import account
from passport_client import federation
from passport_core import password as password_confirmation
from werkzeug.exceptions import Forbidden
from werkzeug.wrappers import Response


class RecoveryTests(unittest.TestCase):
	def setUp(self):
		frappe.local.request = SimpleNamespace(path="/login")

	def tearDown(self):
		frappe.local.request = None

	def test_terminal_cookie_reaches_guest_fallback(self):
		for path in ("/login", "/passport/login", "/passport/status"):
			frappe.local.request.path = path
			with patch.object(federation.store, "get", return_value={"revoked": True}):
				self.assertIs(federation.validate("old-sid", None), False)

	def test_live_expired_lease_is_not_converted_to_guest(self):
		row = {"exp": time.time() - 1}
		with (
			patch.object(federation.store, "get", return_value=row),
			patch.object(federation, "get_valid_local_session", return_value={"user": "alice@example.com"}),
			patch("passport_client.auth.session_page", return_value=Response(status=403)),
		):
			frappe.local.request.path = "/api/method/frappe.auth.get_logged_user"
			with self.assertRaises(Forbidden):
				federation.validate("live-sid", {"passport": True})
			for path in ("/passport/retry", "/passport/status", "/api/method/logout"):
				frappe.local.request.path = path
				self.assertIsNone(federation.validate("live-sid", {"passport": True}))


class PasswordTests(unittest.TestCase):
	def setUp(self):
		frappe.local.request_ip = "127.0.0.77"
		self.user_tracker, self.ip_tracker = Mock(), Mock()
		self.tracking = patch(
			"frappe.auth.get_login_attempt_tracker", side_effect=[self.user_tracker, self.ip_tracker]
		)
		self.get_tracker = self.tracking.start()
		self.addCleanup(self.tracking.stop)

	def test_failure_counts_both_trackers(self):
		with patch.object(password_confirmation, "check_password", side_effect=frappe.AuthenticationError):
			with self.assertRaises(frappe.AuthenticationError):
				account.confirm_password("alice@example.com", "wrong")
		self.user_tracker.add_failure_attempt.assert_called_once()
		self.ip_tracker.add_failure_attempt.assert_called_once()

	def test_lockout_happens_before_password_check(self):
		self.get_tracker.side_effect = frappe.SecurityException
		with patch.object(password_confirmation, "check_password") as verify:
			with self.assertRaises(frappe.SecurityException):
				account.confirm_password("alice@example.com", "correct")
			verify.assert_not_called()

	def test_success_is_bound_to_current_user_and_resets_trackers(self):
		with patch.object(password_confirmation, "check_password") as verify:
			account.confirm_password("alice@example.com", "correct")
			verify.assert_called_once_with("alice@example.com", "correct", delete_tracker_cache=False)
		self.user_tracker.add_success_attempt.assert_called_once()
		self.ip_tracker.add_success_attempt.assert_called_once()

	def test_empty_and_oversized_passwords_count_as_failures(self):
		for password in ("", "x" * 513):
			self.get_tracker.side_effect = [self.user_tracker, self.ip_tracker]
			with patch.object(password_confirmation, "check_password") as verify:
				with self.assertRaises(frappe.AuthenticationError):
					account.confirm_password("alice@example.com", password)
				verify.assert_not_called()
		self.assertEqual(self.user_tracker.add_failure_attempt.call_count, 2)
		self.assertEqual(self.ip_tracker.add_failure_attempt.call_count, 2)


if __name__ == "__main__":
	unittest.main()
