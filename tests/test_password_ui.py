"""Issuer password page routing without replacing native authentication."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from passport import login


class PasswordPageTests(unittest.TestCase):
	def setUp(self):
		frappe.local.request = SimpleNamespace(path="/login", method="GET", args={})
		frappe.local.session = frappe._dict(user="Guest")
		frappe.local.conf = frappe._dict()

	def tearDown(self):
		for key in ("request", "session", "conf"):
			setattr(frappe.local, key, None)

	def test_route_is_get_only_and_does_not_capture_native_api(self):
		frappe.request.path = "/api/method/login"
		self.assertIsNone(login.response())
		frappe.request.path = "/login"
		frappe.request.method = "POST"
		self.assertEqual(login.response().status_code, 405)

	def test_authenticated_redirect_is_local(self):
		frappe.session.user = "alice@example.com"
		frappe.request.args = {"redirect-to": "https://elsewhere.test"}
		self.assertEqual(login.response().location, "/account")

	def test_page_honors_native_password_policy_and_retains_authorization(self):
		destination = "/oidc/authorize?client_id=site1.localhost&state=test"
		frappe.request.args = {"redirect-to": destination}
		frappe.conf.passport_email_code_key = "configured"
		with (
			patch.object(login, "assets", return_value={}),
			patch.object(frappe, "get_system_settings", return_value=True),
			patch.object(frappe, "render_template", return_value="page") as render,
		):
			response = login.response()
			data = render.call_args.args[1]
			self.assertFalse(data["password_enabled"])
			self.assertEqual(data["destination"], destination)
			self.assertTrue(data["email_url"].startswith("/passport/email?redirect-to="))
			self.assertEqual(response.headers["Cache-Control"], "no-store")
