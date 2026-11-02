import json
import os
import subprocess
import sys
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import frappe
from invitation_integration import InvitationTests
from passport_client import store
from passport_core.store import native_session_id


class NoRedirect(HTTPRedirectHandler):
	def redirect_request(self, request, file, code, message, headers, url):
		return None


class InvitationHTTPTests(InvitationTests):
	def test_http_native_login_payload_with_guest_cookie(self):
		from frappe.utils.password import update_password

		password = "fixture-password-only-" + self.user
		update_password(self.user, password, logout_all_sessions=False)
		frappe.db.commit()
		for extra, expected in (
			({"cmd": "logout"}, 403),
			({"cmd": "login", "sid": "foreign-session"}, 403),
			({"cmd": "login"}, 200),
		):
			with self.subTest(fields=tuple(extra), expected=expected):
				request = Request(
					self.server_url + "/api/method/login",
					data=urlencode({"usr": self.user, "pwd": password, **extra}).encode(),
					headers={"Host": "test.local", "Cookie": "sid=Guest", "Accept": "application/json"},
				)
				try:
					response = build_opener(NoRedirect()).open(request, timeout=10)
				except HTTPError as error:
					response = error
				with response:
					self.assertEqual(response.status, expected)
					if expected == 200:
						cookies = SimpleCookie()
						for value in response.headers.get_all("Set-Cookie", []):
							cookies.load(value)
						self.assertNotIn(cookies["sid"].value, ("", "Guest"))
						frappe.db.rollback()
						self.assertTrue(
							frappe.db.sql(
								"SELECT sid FROM tabSessions WHERE sid=%s AND user=%s",
								(native_session_id(cookies["sid"].value), self.user),
							)
						)

	def test_http_admin_actions_reject_non_managers(self):
		name, _, _, _, _, sid, csrf = self.prepare_http()
		for endpoint, fields in (
			("send_invitation", {"user": self.user}),
			("cancel_invitation", {"name": name}),
		):
			status, _, _ = self.http("POST", endpoint, sid, fields, csrf=csrf)
			self.assertEqual(status, 403)
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Pending")

	def test_http_admin_queue_cancel_and_duplicate_submission(self):
		doc = frappe.get_doc("User", self.user)
		doc.append("roles", {"role": "System Manager"})
		doc.save(ignore_permissions=True)
		name, _, _, _, _, sid, csrf = self.prepare_http()
		for endpoint, fields in (
			("send_invitation", {"user": self.user}),
			("cancel_invitation", {"name": name}),
		):
			for method, session, token in (("GET", sid, csrf), ("POST", sid, ""), ("POST", "Guest", csrf)):
				with self.subTest(
					endpoint=endpoint, method=method, authenticated=session != "Guest", csrf=bool(token)
				):
					status, _, _ = self.http(method, endpoint, session, fields, csrf=token)
					self.assertIn(status, (400, 401, 403, 405))
		for _ in range(2):
			status, _, _ = self.http("POST", "cancel_invitation", sid, {"name": name}, csrf=csrf)
			self.assertEqual(status, 200)
		status, _, body = self.http("POST", "send_invitation", sid, {"user": self.user}, csrf=csrf)
		self.assertEqual(status, 200)
		result = json.loads(body)["message"]
		self.assertEqual(set(result), {"name"})
		self.assertNotEqual(result["name"], name)
		status, _, body = self.http("POST", "send_invitation", sid, {"user": self.user}, csrf=csrf)
		self.assertEqual(status, 200)
		self.assertEqual(json.loads(body)["message"], result)
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Cancelled")
		self.assertEqual(
			frappe.db.count(
				"Email Queue", {"reference_doctype": "Passport Invitation", "reference_name": result["name"]}
			),
			1,
		)

	def test_http_admin_send_rejects_compatibility_policy(self):
		doc = frappe.get_doc("User", self.user)
		doc.append("roles", {"role": "System Manager"})
		doc.save(ignore_permissions=True)
		_, _, _, _, _, sid, csrf = self.prepare_http()
		store.put("admission_policy", "site", {"mode": "compatibility"})
		frappe.db.commit()
		status, _, _ = self.http("POST", "send_invitation", sid, {"user": self.user}, csrf=csrf)
		self.assertIn(status, (400, 417))

	def test_http_invitation_page_cleans_url_and_accepts_only_on_post(self):
		name, identity, attempt, secret, browser, sid, csrf = self.prepare_http()

		def request(path, cookie="", data=None):
			query = Request(
				self.server_url + path,
				data=urlencode(data).encode() if data else None,
				headers={
					"Host": "test.local",
					"Cookie": f"sid={sid}; passport_browser={browser}; {cookie}",
					"Content-Type": "application/x-www-form-urlencoded",
				},
			)
			try:
				result = build_opener(NoRedirect()).open(query, timeout=10)
			except HTTPError as error:
				result = error
			with result:
				return result.status, result.headers, result.read()

		status, headers, _ = request("/passport/invitation?" + urlencode({"name": name, "secret": secret}))
		self.assertEqual(status, 303)
		self.assertEqual(headers["Location"], "/passport/invitation")
		self.assertIn("HttpOnly", headers["Set-Cookie"])
		cookies = SimpleCookie(headers["Set-Cookie"])
		cookie = "passport_invitation=" + cookies["passport_invitation"].coded_value
		status, headers, body = request("/passport/invitation", cookie)
		self.assertEqual(status, 200)
		self.assertIn(b"Connect your Frappe ID", body)
		self.assertNotIn(secret.encode(), body)
		status, _, body = request("/passport/invitation?" + urlencode({"attempt": attempt}), cookie)
		self.assertEqual(status, 200)
		self.assertIn(b"Accept your invitation", body)
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)
		status, _, _ = request("/passport/invitation", cookie, {"action": "accept", "attempt": attempt})
		self.assertIn(status, (400, 403))
		status, _, _ = request(
			"/passport/invitation", cookie, {"action": "accept", "attempt": attempt, "csrf_token": "wrong"}
		)
		self.assertIn(status, (400, 403))
		status, headers, body = request(
			"/passport/invitation", cookie, {"action": "accept", "attempt": attempt, "csrf_token": csrf}
		)
		self.assertEqual(status, 200)
		self.assertIn(b"Invitation accepted", body)
		self.assertIn("Max-Age=0", headers["Set-Cookie"])
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 1)

	def prepare_http(self):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		csrf = "fixture-csrf-token"
		data = {
			"session_expiry": "06:00:00",
			"passport_generation": store.generation(self.user),
			"csrf_token": csrf,
		}
		frappe.db.sql(
			"UPDATE tabSessions SET sessiondata=%s WHERE sid=%s", (json.dumps(data), native_session_id(sid))
		)
		frappe.db.commit()
		return name, identity, attempt, secret, browser, sid, csrf

	def http(self, method, endpoint, sid, fields, *, csrf="", browser=""):
		body = urlencode(fields).encode() if method == "POST" else None
		url = self.server_url + "/api/method/passport_client.invitations." + endpoint
		if method == "GET":
			url += "?" + urlencode(fields)
		request = Request(
			url,
			data=body,
			method=method,
			headers={
				"Host": "test.local",
				"Cookie": f"sid={sid}; passport_browser={browser}",
				"Content-Type": "application/x-www-form-urlencoded",
				"X-Frappe-CSRF-Token": csrf,
				"Accept": "application/json",
			},
		)
		try:
			response = build_opener(NoRedirect()).open(request, timeout=10)
		except HTTPError as error:
			response = error
		with response:
			return response.status, response.headers, response.read()

	def test_http_complete_requires_post_csrf_cookie_and_native_session(self):
		name, identity, attempt, secret, browser, sid, csrf = self.prepare_http()
		fields = {"attempt_id": attempt, "secret": secret}
		for method, session, token, cookie in (
			("GET", sid, csrf, browser),
			("POST", sid, "", browser),
			("POST", sid, csrf, "wrong"),
			("POST", "Guest", csrf, browser),
		):
			with self.subTest(
				method=method, authenticated=session != "Guest", csrf=bool(token), cookie=cookie == browser
			):
				status, _, _ = self.http(method, "complete", session, fields, csrf=token, browser=cookie)
				self.assertIn(status, (400, 401, 403, 405))
		status, headers, body = self.http("POST", "complete", sid, fields, csrf=csrf, browser=browser)
		self.assertEqual(status, 200)
		self.assertEqual(json.loads(body), {"accepted": True})
		self.assertEqual(headers.get("Cache-Control"), "no-store")
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Accepted")
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 1)

	def test_http_begin_checks_real_local_password(self):
		from frappe.utils.password import update_password

		name, _, _, secret, browser, sid, csrf = self.prepare_http()
		password = "fixture-password-only-" + self.user
		update_password(self.user, password, logout_all_sessions=False)
		frappe.db.commit()
		fields = {"name": name, "secret": secret, "password": "wrong"}
		status, _, _ = self.http("POST", "begin", sid, fields, csrf=csrf, browser=browser)
		self.assertIn(status, (401, 403))
		fields["password"] = password
		status, headers, _ = self.http("POST", "begin", sid, fields, csrf=csrf, browser=browser)
		self.assertIn(status, (302, 303))
		self.assertTrue(headers["Location"].startswith("https://issuer.example.invalid/oidc/authorize?"))
		self.assertIn("passport_browser=", headers.get("Set-Cookie", ""))
		self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")


if __name__ == "__main__":
	sites = Path("/tmp/passport-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	server_code = """
import frappe
from passport_core.store import native_session_id
import frappe.app
from werkzeug.serving import make_server
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.doctype.email_queue.email_queue import EmailQueue
EmailAccount.find_outgoing = staticmethod(lambda **kwargs: frappe.get_doc({
    'doctype': 'Email Account', 'email_id': 'sender@example.invalid', 'email_account_name': 'Fixture'}))
def forbid_smtp(*args, **kwargs):
    raise AssertionError('Unexpected SMTP in HTTP fixture')
EmailQueue.send = forbid_smtp
original = frappe.init
def initialize(*args, **kwargs):
    result = original(*args, **kwargs)
    frappe.conf.update(passport_allow_linking=True, passport_disabled=False, ignore_csrf=False,
        passport_issuer='https://issuer.example.invalid', passport_client_id='fixture',
        passport_private_key='not-used-by-these-endpoints', host_name='https://test.local')
    return result
frappe.init = initialize
frappe.app._site = 'test.local'
server = make_server('127.0.0.1', 0, frappe.app.application)
print(server.server_port, flush=True)
server.serve_forever()
"""
	with open("/tmp/passport-invitation-http-server.log", "w") as log:
		server = subprocess.Popen(
			[sys.executable, "-c", server_code],
			cwd=sites,
			env={**os.environ, "SITES_PATH": str(sites)},
			stdout=subprocess.PIPE,
			stderr=log,
			text=True,
		)
		try:
			InvitationHTTPTests.server_url = "http://127.0.0.1:" + server.stdout.readline().strip()
			frappe.init(site="test.local", sites_path=str(sites))
			frappe.connect()
			frappe.in_test = True
			tests = [
				InvitationHTTPTests(name)
				for name in unittest.defaultTestLoader.getTestCaseNames(InvitationHTTPTests)
				if name.startswith("test_http_")
			]
			result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
			if not result.wasSuccessful():
				raise SystemExit(1)
		finally:
			frappe.db.rollback()
			frappe.destroy()
			server.terminate()
			server.wait(timeout=10)
