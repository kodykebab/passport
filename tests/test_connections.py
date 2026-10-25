"""Site revocation generations and isolation at issuer protocol boundaries."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport import connections, consent, oidc
from werkzeug.datastructures import MultiDict


class ConnectionTests(unittest.TestCase):
	def setUp(self):
		self.records = {}
		self.get = patch.object(
			connections.store, "get", side_effect=lambda kind, key, **kw: self.records.get((kind, key))
		)
		self.put = patch.object(
			connections.store,
			"put",
			side_effect=lambda kind, key, data: self.records.__setitem__((kind, key), dict(data)),
		)
		self.get.start()
		self.put.start()
		self.addCleanup(self.get.stop)
		self.addCleanup(self.put.stop)

	def test_reenable_never_revives_old_generation(self):
		connections.remember("user", "site")
		self.assertEqual(connections.check("user", "site", 0), 0)
		connections.set_revoked("user", "site", True)
		connections.set_revoked("user", "site", True)
		with self.assertRaises(ValueError):
			connections.check("user", "site")
		connections.set_revoked("user", "site", False)
		self.assertEqual(connections.check("user", "site"), 1)
		with self.assertRaises(ValueError):
			connections.check("user", "site", 0)
		self.assertEqual(connections.check("another-user", "site", 0), 0)
		self.assertEqual(connections.check("user", "another-site", 0), 0)

	def test_legacy_login_can_be_revoked_but_unknown_site_cannot(self):
		with patch.object(connections.store, "rows", return_value=[("sid", {"client": "legacy"})]):
			connections.set_revoked("user", "legacy", True)
			with self.assertRaises(ValueError):
				connections.check("user", "legacy", 0)
			with self.assertRaisesRegex(ValueError, "Unknown"):
				connections.set_revoked("user", "unknown", False)

	def test_pending_completion_and_renewal_check_generation_before_signing(self):
		frappe.local.request = SimpleNamespace(path="/passport/complete")
		self.addCleanup(setattr, frappe.local, "request", None)
		connections.remember("user", "site")
		connections.set_revoked("user", "site", True)
		connections.set_revoked("user", "site", False)
		for binding in ({}, {"binding": "binding"}):
			self.records[("login", "sid")] = {
				"client": "site",
				"user": "user",
				"issuer_sid": "native",
				"revoked": False,
				"deadline": time.time() + 3600,
				"pending_until": time.time() + 120,
				**binding,
			}
			with (
				patch.object(oidc.store, "get_valid_native_session", return_value={"user": "user"}),
				patch.object(oidc, "signing") as sign,
			):
				with self.assertRaisesRegex(ValueError, "site_access_revoked"):
					oidc.lease(SimpleNamespace(name="site"), {"sid": "sid", "binding": "binding"})
				sign.assert_not_called()

	def test_revocation_blocks_pending_code_exchange(self):
		connections.remember("user", "site")
		connections.set_revoked("user", "site", True)
		self.records[("grant", oidc.protocol.digest(b"code"))] = {
			"client": "site",
			"user": "user",
			"issuer_sid": "native",
			"exp": time.time() + 60,
			"redirect_uri": "https://site.example/callback",
			"challenge": oidc.protocol.pkce_challenge("verifier"),
		}
		with (
			patch.object(oidc.store, "get_valid_native_session", return_value={"user": "user"}),
			patch.object(oidc, "signing") as sign,
		):
			with self.assertRaisesRegex(ValueError, "site_access_revoked"):
				oidc.token(
					SimpleNamespace(name="site"),
					{
						"code": "code",
						"code_verifier": "verifier",
						"redirect_uri": "https://site.example/callback",
					},
				)
			sign.assert_not_called()


class PendingConnectionTests(unittest.TestCase):
	def test_reenabled_site_rejects_pre_revocation_consent(self):
		frappe.local.session = frappe._dict(user="user", sid="native", data=frappe._dict(csrf_token="csrf"))
		frappe.local.request = SimpleNamespace(
			form=MultiDict({"csrf_token": "csrf", "request_id": "request", "decision": "allow"})
		)
		row = {
			"user": "user",
			"issuer_sid": "native",
			"client": "site",
			"exp": time.time() + 600,
			"connection_generation": 0,
		}
		try:
			with (
				patch.object(consent.store, "get_valid_native_session", return_value={"user": "user"}),
				patch.object(
					consent.store,
					"get",
					side_effect=lambda kind, key, **kw: (
						row if kind == "link_consent" else {"generation": 1, "revoked": False}
					),
				),
				patch.object(consent, "render_page", return_value="expired"),
				patch.object(oidc, "issue_grant") as grant,
			):
				self.assertEqual(consent.submit().status_code, 403)
				grant.assert_not_called()
		finally:
			frappe.local.session = frappe.local.request = None

	def test_new_authorization_for_revoked_site_shows_recovery(self):
		frappe.local.db = Mock()
		frappe.db.get_value.return_value = frappe._dict(
			name="site", redirect_uri="https://site.example/callback", title="Site"
		)
		frappe.local.session = frappe._dict(user="user", sid="native")
		frappe.local.request = SimpleNamespace(
			args={
				"client_id": "site",
				"redirect_uri": "https://site.example/callback",
				"response_type": "code",
				"code_challenge_method": "S256",
				"code_challenge": "challenge",
				"nonce": "nonce",
				"state": "state",
			}
		)
		try:
			with (
				patch.object(oidc.store, "get_valid_native_session", return_value={"user": "user"}),
				patch.object(connections.store, "get", return_value={"revoked": True, "generation": 1}),
				patch.object(connections, "render_page", return_value="Manage connected sites") as render,
				patch.object(oidc, "issue_grant") as grant,
			):
				self.assertEqual(oidc.authorize().status_code, 403)
				self.assertEqual(render.call_args.args[2]["link"], "/account")
				grant.assert_not_called()
		finally:
			frappe.local.db = frappe.local.session = frappe.local.request = None
