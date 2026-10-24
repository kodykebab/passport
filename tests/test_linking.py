"""Linking policy, session binding and single-use issuer consent."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport import consent, oidc
from passport_client import auth, linking
from werkzeug.datastructures import MultiDict


class LinkingTests(unittest.TestCase):
	def setUp(self):
		frappe.local.conf = frappe._dict(passport_allow_linking=True)
		frappe.local.db = Mock()
		frappe.local.session = frappe._dict(
			user="local@example.test", sid="local-session", data=frappe._dict(csrf_token="csrf")
		)
		self.config = patch.object(auth, "config", return_value=frappe._dict(issuer="https://id.example"))
		self.config.start()
		self.addCleanup(self.config.stop)

	def tearDown(self):
		for key in ("conf", "db", "session", "request"):
			setattr(frappe.local, key, None)

	def test_only_enabled_native_password_sessions_can_link(self):
		with (
			patch.object(frappe, "get_system_settings", return_value=False),
			patch.object(
				linking.store, "get_valid_native_session", return_value={"user": "local@example.test"}
			) as native,
			patch.object(linking.store, "get", return_value=None),
		):
			self.assertEqual(linking.require_local_user(), "local@example.test")
			for marker in ({"passport": True}, None):
				native.return_value = marker
				with self.assertRaises(auth.PassportError):
					linking.require_local_user()
			frappe.conf.passport_allow_linking = False
			with self.assertRaises(auth.PassportError):
				linking.require_local_user()

	def test_session_generation_expiry_and_existing_links_prevent_insert(self):
		attempt = {
			"user": "local@example.test",
			"local_sid": "local-session",
			"generation": 0,
			"exp": time.time() + 600,
		}
		with (
			patch.object(linking, "require_local_user", return_value=attempt["user"]),
			patch.object(linking.store, "generation", return_value=0),
			patch.object(frappe, "get_doc") as create,
		):
			for changes in ({"local_sid": "other"}, {"user": "other"}, {"generation": 1}, {"exp": 0}):
				with self.assertRaises(auth.PassportError):
					linking.finish({**attempt, **changes}, {"sub": "subject"})
			frappe.db.sql.return_value = [("existing",)]
			with self.assertRaises(auth.PassportError):
				linking.finish(attempt, {"sub": "subject"})
			create.assert_not_called()

	def test_consent_rejects_csrf_replay_expiry_and_session_swap(self):
		row = {
			"user": frappe.session.user,
			"issuer_sid": frappe.session.sid,
			"exp": time.time() + 600,
			"client": "client",
			"args": {"redirect_uri": "https://site.example/passport/callback", "state": "state"},
		}
		frappe.local.request = SimpleNamespace(
			form=MultiDict({"csrf_token": "wrong", "request_id": "request", "decision": "allow"})
		)
		with (
			patch.object(consent, "render_page", return_value="error"),
			patch.object(
				consent.store, "get_valid_native_session", return_value={"user": "local@example.test"}
			),
			patch.object(consent.store, "get", return_value=row) as get,
			patch.object(oidc, "issue_grant") as grant,
		):
			self.assertEqual(consent.submit().status_code, 403)
			frappe.request.form["csrf_token"] = "csrf"
			for changes in ({"spent": True}, {"exp": 0}, {"issuer_sid": "other"}, {"user": "other"}):
				get.return_value = {**row, **changes}
				self.assertEqual(consent.submit().status_code, 403)
			grant.assert_not_called()

	def test_consent_cancel_spends_request_without_issuing_code(self):
		row = {
			"user": frappe.session.user,
			"issuer_sid": frappe.session.sid,
			"exp": time.time() + 600,
			"client": "client",
			"args": {"redirect_uri": "https://site.example/passport/callback", "state": "state"},
		}
		frappe.local.request = SimpleNamespace(
			form=MultiDict({"csrf_token": "csrf", "request_id": "request", "decision": "cancel"})
		)
		frappe.db.get_value.return_value = frappe._dict(
			name="client", redirect_uri=row["args"]["redirect_uri"]
		)
		with (
			patch.object(
				consent.store, "get_valid_native_session", return_value={"user": "local@example.test"}
			),
			patch.object(consent.store, "get", return_value=row),
			patch.object(consent.store, "put") as put,
			patch.object(oidc, "issue_grant") as grant,
		):
			response = consent.submit()
			self.assertEqual(response.status_code, 303)
			self.assertIn("error=access_denied", response.location)
			self.assertTrue(put.call_args.args[2]["spent"])
			grant.assert_not_called()


class LinkingTokenTests(unittest.TestCase):
	def test_linking_proof_is_not_a_login_token_or_lease_session(self):
		from datetime import datetime, timedelta, timezone

		from passport import protocol

		frappe.local.conf = frappe._dict(passport_issuer="https://id.example")
		frappe.local.db = Mock()
		frappe.db.get_value.return_value = frappe._dict(email="id@example.test", full_name="Name")
		grant = {
			"action": "link",
			"client": "client",
			"redirect_uri": "https://site.example/passport/callback",
			"challenge": protocol.pkce_challenge("verifier"),
			"exp": time.time() + 60,
			"issuer_sid": "issuer-session",
			"user": "id@example.test",
			"sub": "subject",
			"nonce": "nonce",
		}
		native = {
			"session_end": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
			"passport_auth_method": "password",
			"auth_time": int(time.time()),
		}
		try:
			with (
				patch.object(oidc.store, "get", return_value=grant),
				patch.object(oidc.store, "get_valid_native_session", return_value=native),
				patch.object(oidc.store, "put"),
				patch.object(oidc.store, "create") as create,
				patch.object(oidc, "signing", return_value="signed-proof") as sign,
			):
				self.assertEqual(
					oidc.token(
						frappe._dict(name="client"),
						{"code": "code", "redirect_uri": grant["redirect_uri"], "code_verifier": "verifier"},
					),
					{"id_token": "signed-proof"},
				)
				self.assertEqual(sign.call_args.args[1], "passport-link+jwt")
				self.assertTrue(grant["spent"])
				create.assert_not_called()
		finally:
			frappe.local.conf = frappe.local.db = None
