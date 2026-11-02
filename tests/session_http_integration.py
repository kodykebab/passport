import argparse
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import frappe
import frappe.sessions
from passport_core.store import native_session_id

if session_source := os.environ.get("PASSPORT_SESSION_SOURCE"):
	source = Path(session_source)
	exec(compile(source.read_text(), str(source), "exec"), frappe.sessions.__dict__)

from frappe.sessions import get_csrf_token
from passport_client import store
from session_authority_integration import SessionAuthorityTests


class SessionHTTPTests(SessionAuthorityTests):
	def request(self, path, sid, method="GET", csrf_token=""):
		request = Request(
			self.server_url + path,
			method=method,
			headers={
				"Host": "test.local",
				"Cookie": f"sid={sid}",
				"Accept": "application/json",
				"X-Frappe-CSRF-Token": csrf_token,
			},
		)
		try:
			response = urlopen(request, timeout=10)
		except HTTPError as error:
			response = error
		with response:
			return response.status, response.headers, response.read()

	def test_http_valid_federated_session_reaches_authenticated_endpoint(self):
		session = self.create_session()
		frappe.db.commit()
		status, _, body = self.request("/api/method/frappe.auth.get_logged_user", session.sid)
		self.assertEqual(status, 200)
		self.assertEqual(json.loads(body)["message"], self.user)

	def test_http_expired_lease_blocks_endpoint_but_allows_status(self):
		session = self.create_session()
		row = store.get("federation", native_session_id(session.sid))
		row["exp"] = time.time() - 1
		store.put("federation", native_session_id(session.sid), row)
		frappe.db.commit()
		status, _, body = self.request("/api/method/frappe.auth.get_logged_user", session.sid)
		self.assertEqual(status, 403)
		self.assertIn(b"Session validation required", body)
		status, headers, _ = self.request("/passport/status", session.sid)
		self.assertEqual(status, 200)
		self.assertIn("no-store", headers["Cache-Control"])

	def test_http_logout_ends_federated_session(self):
		session = self.create_session()
		csrf_token = get_csrf_token()
		session.update(force=True)
		frappe.db.commit()
		status, _, _ = self.request("/api/method/logout", session.sid, method="POST")
		self.assertEqual(status, 400)
		status, _, _ = self.request("/api/method/logout", session.sid, method="POST", csrf_token=csrf_token)
		self.assertEqual(status, 200)
		status, _, body = self.request("/api/method/frappe.auth.get_logged_user", session.sid)
		self.assertIn(status, (401, 403))
		self.assertNotIn(self.user.encode(), body)

	def test_http_session_parameter_injection_is_rejected(self):
		session = self.create_session()
		frappe.db.commit()
		status, _, body = self.request("/passport/status?sid=untrusted", session.sid)
		self.assertEqual(status, 403)
		self.assertIn(b"Invalid session parameters", body)

	def test_http_native_session_remains_usable(self):
		session = self.create_session(federated=False)
		frappe.db.commit()
		status, _, body = self.request("/api/method/frappe.auth.get_logged_user", session.sid)
		self.assertEqual(status, 200)
		self.assertEqual(json.loads(body)["message"], self.user)

	def test_http_disable_reenable_cannot_reuse_warm_session(self):
		session = self.create_session()
		frappe.db.commit()
		status, _, _ = self.request("/api/method/frappe.auth.get_logged_user", session.sid)
		self.assertEqual(status, 200)
		frappe.set_user("Administrator")
		user = frappe.get_doc("User", self.user)
		user.enabled = 0
		user.save(ignore_permissions=True)
		frappe.db.commit()
		user.enabled = 1
		user.save(ignore_permissions=True)
		frappe.db.commit()
		status, _, body = self.request("/api/method/frappe.auth.get_logged_user", session.sid)
		self.assertIn(status, (401, 403))
		self.assertNotIn(self.user.encode(), body)


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--sites-path", required=True, type=Path)
	args = parser.parse_args()
	sites = args.sites_path.resolve()
	marker = sites / "test.local" / ".passport-acceptance-fixture"
	if marker.read_text().strip() != "disposable-passport-test-site":
		parser.error("Disposable test.local marker required")
	environment = {**os.environ, "SITES_PATH": str(sites)}
	server = subprocess.Popen(
		[
			sys.executable,
			"-c",
			"import os; from pathlib import Path; import frappe.sessions; "
			"source = os.environ.get('PASSPORT_SESSION_SOURCE'); "
			"exec(compile(Path(source).read_text(), source, 'exec'), frappe.sessions.__dict__) if source else None; "
			"import frappe.app; from werkzeug.serving import make_server; "
			"frappe.app._site = 'test.local'; "
			"server = make_server('127.0.0.1', 0, frappe.app.application); "
			"print(server.server_port, flush=True); server.serve_forever()",
		],
		cwd=sites,
		env=environment,
		stdout=subprocess.PIPE,
		text=True,
	)
	try:
		SessionHTTPTests.server_url = f"http://127.0.0.1:{int(server.stdout.readline().strip())}"
		frappe.init(site="test.local", sites_path=str(sites))
		frappe.connect()
		frappe.in_test = True
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(SessionHTTPTests)
		)
		if not result.wasSuccessful():
			raise SystemExit(1)
	finally:
		frappe.destroy()
		server.terminate()
		server.wait(timeout=10)


if __name__ == "__main__":
	main()
