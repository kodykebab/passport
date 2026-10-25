"""Picker navigation accepts only usable registered callback origins."""

import unittest
from unittest.mock import Mock, patch

import frappe
from passport import connections
from passport.email_login import return_path


class SitePickerTests(unittest.TestCase):
	def test_invalid_callback_cannot_become_navigation(self):
		for callback in (
			"javascript:alert(1)",
			"//example.com/passport/callback",
			"https://user:secret@example.com/passport/callback",
			"https://@example.com/passport/callback",
			"https://example.com:bad/passport/callback",
			"https://example.com/other",
			"https://example.com/passport/callback?next=evil",
			"https://example.com/passport/callback#x",
			"https://example.com\\@evil/passport/callback",
			"https://example.com/\npassport/callback",
		):
			with self.subTest(callback=callback):
				self.assertIsNone(connections.site_origin(callback))
		self.assertEqual(
			connections.site_origin("http://site1.localhost:8080/passport/callback"),
			"http://site1.localhost:8080",
		)
		self.assertEqual(return_path("/sites"), "/sites")

	def test_revoked_disabled_and_unknown_sites_have_no_login_link(self):
		frappe.local.db = Mock()
		try:
			for registered, revoked, allowed in (
				(None, False, False),
				(True, False, True),
				(True, True, False),
				(False, False, False),
			):
				frappe.db.get_value.return_value = (
					None
					if registered is None
					else frappe._dict(
						title="Site",
						redirect_uri="https://site.example/passport/callback",
						enabled=registered,
					)
				)
				with patch.object(
					connections.store,
					"rows",
					side_effect=lambda kind, **kw: (
						[("key", {"client": "site", "revoked": revoked})] if kind == "connection" else []
					),
				):
					row = connections.sites("user")[0]
					self.assertEqual(bool(row["login_url"]), allowed)
					if allowed:
						self.assertEqual(row["login_url"], "https://site.example/passport/login")
		finally:
			frappe.local.db = None
