"""Interrupted completion must remove the pending native login and its cookie."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport_client import auth, protocol


class CompletionFailureTests(unittest.TestCase):
	def test_completion_failure_cleans_up_pending_session(self):
		frappe.local.session = frappe._dict(user="Guest")
		frappe.local.flags = frappe._dict()
		frappe.local.db = Mock()
		frappe.local.request = SimpleNamespace(
			args={"state": "state", "code": "code"}, cookies={"passport_browser": "browser"}
		)

		def login_as(user, **kwargs):
			frappe.local.session = frappe._dict(user=user, sid="pending-session")

		frappe.local.login_manager = SimpleNamespace(login_as=login_as)
		attempt = {
			"browser": protocol.digest(b"browser"),
			"exp": time.time() + 60,
			"verifier": "verifier",
			"nonce": "nonce",
			"return": "/desk",
		}
		claims = {
			"nonce": "nonce",
			"amr": ["pwd"],
			"passport_auth_method": "password",
			"iat": int(time.time()),
			"exp": int(time.time()) + 60,
			"issuer_session_expires_at": int(time.time()) + 3600,
			"sid": "issuer-login",
			"sub": "subject",
		}
		try:
			with (
				patch.object(auth.store, "get", return_value=attempt),
				patch.object(auth.store, "put"),
				patch.object(
					auth, "config", return_value=SimpleNamespace(issuer="issuer", redirect_uri="callback")
				),
				patch.object(auth, "call", return_value={"id_token": "token"}),
				patch.object(auth, "verify", return_value=claims),
				patch.object(auth, "resolve_user", return_value="alice@example.com"),
				patch("passport_client.federation.renew", side_effect=auth.PassportError("offline")),
				patch("frappe.sessions.delete_session") as delete,
				patch("frappe.auth.clear_cookies") as clear,
			):
				with self.assertRaisesRegex(auth.PassportError, "offline"):
					auth.callback()
				delete.assert_called_once_with(
					"pending-session", user="alice@example.com", reason="Frappe ID completion failed"
				)
				clear.assert_called_once()
				self.assertTrue(attempt["spent"])
		finally:
			for key in ("session", "flags", "db", "request", "login_manager"):
				setattr(frappe.local, key, None)
