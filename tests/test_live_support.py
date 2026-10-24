"""Test browser cleanup retains every issued session, even after a failed login."""

import unittest
from unittest.mock import patch

import requests
from live_support import BrowserSessions


def response(site, sid):
	result = requests.Response()
	result.url = f"http://{site}:8080/login"
	result.cookies.set("sid", sid)
	return result


class BrowserSessionTests(unittest.TestCase):
	def test_replaced_cookies_and_failed_login_are_cleaned(self):
		with patch.object(BrowserSessions, "delete_session") as delete:
			with self.assertRaisesRegex(RuntimeError, "failed"):
				with BrowserSessions() as sessions:
					browser = sessions.browser()
					remember = browser.hooks["response"][0]
					remember(response("id.localhost", "first"))
					remember(response("id.localhost", "second"))
					remember(response("id.localhost", "second"))
					remember(response("site1.localhost", "client"))
					remember(response("id.localhost", "Guest"))
					remember(response("foreign.test", "unrelated"))
					raise RuntimeError("failed")
			self.assertCountEqual(
				[call.args for call in delete.call_args_list],
				[("id.localhost", "first"), ("id.localhost", "second"), ("site1.localhost", "client")],
			)

	def test_cleanup_continues_when_one_session_delete_fails(self):
		with patch.object(
			BrowserSessions, "delete_session", side_effect=[RuntimeError("failed"), None]
		) as delete:
			with self.assertRaises(RuntimeError):
				with BrowserSessions() as sessions:
					sessions.remember_sessions(response("id.localhost", "issuer"))
					sessions.remember_sessions(response("site1.localhost", "client"))
			self.assertEqual(delete.call_count, 2)
