"""Native website RPC remains subject to Passport's session boundary."""

import unittest
from types import SimpleNamespace

import frappe
from passport_client import federation
from werkzeug.exceptions import Forbidden


class ClientBoundaryTests(unittest.TestCase):
	def setUp(self):
		frappe.local.request = SimpleNamespace(path="/", cookies={"sid": "browser"})
		frappe.local.form_dict = frappe._dict(cmd="frappe.website.doctype.web_form.web_form.accept")
		frappe.local.flags = frappe._dict()
		for key in ("request", "form_dict", "flags"):
			self.addCleanup(setattr, frappe.local, key, None)

	def test_native_root_rpc_is_not_background_activity(self):
		federation.boundary()
		self.assertFalse(frappe.flags.passport_background)

	def test_cmd_cannot_override_protocol_or_api_routes(self):
		for path in ("/passport/retry", "/passport/status", "/passport/callback", "/api/method/login"):
			with self.subTest(path=path):
				frappe.request.path = path
				with self.assertRaises(Forbidden):
					federation.boundary()

	def test_root_rpc_cannot_override_browser_session(self):
		frappe.form_dict.sid = "foreign-session"
		with self.assertRaises(Forbidden):
			federation.boundary()
