"""Real SQL/cache/HTTP lease tests; mutates only test browser sessions."""

import contextlib
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT / ".bench/sites")
import frappe
from passport_client import store as store

ID = "http://id.localhost:8080"
SITE = "http://site1.localhost:8080"
USER = "alice@example.com"


@contextlib.contextmanager
def db(site="site1.localhost"):
	frappe.init(site=site, sites_path=str(ROOT / ".bench/sites"))
	frappe.connect()
	try:
		yield
		frappe.db.commit()
	finally:
		frappe.destroy()


def check(label, value):
	assert value, label
	print("PASS", label, flush=True)


def get(b):
	return b.get(SITE + "/api/method/frappe.auth.get_logged_user")


def renew(b, csrf):
	return b.post(SITE + "/passport/retry", headers={"X-Frappe-CSRF-Token": csrf})


class BrowserSessions:
	"""Own all issued test sessions, including cookies replaced by later responses."""

	def __init__(self):
		self.stack = contextlib.ExitStack()
		self.sessions = set()

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return self.stack.__exit__(*exc)

	def browser(self):
		browser = self.stack.enter_context(requests.Session())
		browser.hooks["response"].append(self.remember_sessions)
		return browser

	def login(self):
		browser = self.browser()
		response = browser.get(SITE + "/passport/login")
		path = parse_qs(urlparse(response.url).query)["redirect-to"][0]
		assert browser.post(ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"}).ok
		assert browser.get(ID + path).ok
		response = browser.get(SITE + "/desk")
		csrf = re.search(r'csrf_token = "([^"]+)"', response.text).group(1)
		return browser, csrf, browser.cookies.get("sid", domain="site1.localhost")

	def remember_sessions(self, response, **kwargs):
		site = urlparse(response.url).hostname
		if site not in ("id.localhost", "site1.localhost"):
			return
		for cookie in response.cookies:
			key = (site, cookie.value)
			if cookie.name == "sid" and cookie.value not in ("", "Guest") and key not in self.sessions:
				self.sessions.add(key)
				self.stack.callback(self.delete_session, *key)

	@staticmethod
	def delete_session(site, sid):
		from frappe.sessions import delete_session

		with db(site):
			if frappe.db.exists("Sessions", {"sid": sid, "user": USER}):
				delete_session(sid, user=USER, reason="Integration test cleanup")
