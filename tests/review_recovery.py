"""HTTP recovery after terminal native expiry, plus real Redis reauthentication limits."""

import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from live_support import ID, SITE, USER, BrowserSessions, check, db
from passport import account

with BrowserSessions() as sessions:
	for cached in (True, False):
		browser, csrf, sid = sessions.login()
		with db():
			data = json.loads(frappe.db.sql("SELECT sessiondata FROM tabSessions WHERE sid=%s", sid)[0][0])
			data["session_end"] = datetime.fromtimestamp(time.time() - 1, timezone.utc).isoformat()
			frappe.db.sql("UPDATE tabSessions SET sessiondata=%s WHERE sid=%s", (json.dumps(data), sid))
			if cached:
				entry = frappe.cache.hget("session", sid)
				entry["data"]["session_end"] = data["session_end"]
				frappe.cache.hset("session", sid, entry)
			else:
				frappe.cache.hdel("session", sid)
		# Reject the original protected operation; never retry it after authentication.
		response = browser.post(
			SITE + "/api/method/frappe.client.insert", json={"doc": {}}, headers={"X-Frappe-CSRF-Token": csrf}
		)
		check("terminal cookie cannot authorize a mutation", response.status_code in (401, 403))
		check("terminal cookie reaches login (cache=" + str(cached) + ")", browser.get(SITE + "/login").ok)
		check("expired cookie replaced", browser.cookies.get("sid", domain="site1.localhost") != sid)
		response = browser.get(SITE + "/passport/login", allow_redirects=False)
		check(
			"new SSO attempt reachable",
			response.status_code == 302 and "/oidc/authorize" in response.headers["Location"],
		)

	with db("id.localhost"):
		from frappe.auth import get_login_attempt_tracker

		prefix = "review-throttle-" + uuid.uuid4().hex
		frappe.local.request_ip = "127.0.0.77"
		original_get_doc = frappe.get_doc

		def settings(*args, **kwargs):
			if args == ("System Settings",):
				return SimpleNamespace(allow_consecutive_login_attempts=2, allow_login_after_fail=60)
			return original_get_doc(*args, **kwargs)

		def tracker(key, **kwargs):
			return get_login_attempt_tracker(prefix + key, **kwargs)

		try:
			with (
				patch("frappe.get_doc", side_effect=settings),
				patch("frappe.auth.get_login_attempt_tracker", side_effect=tracker),
			):
				for _ in range(3):
					try:
						account.confirm_password(USER, "incorrect-review-password")
						raise AssertionError("Wrong password accepted")
					except frappe.AuthenticationError:
						pass
				for key in (USER, frappe.local.request_ip):
					assert tracker(key, raise_locked_exception=False).login_failed_count == 3
				try:
					account.confirm_password(USER, "alice-passport-1")
					raise AssertionError("Lockout bypassed by correct password")
				except frappe.SecurityException:
					check("real native user/IP lockout enforced for account actions", True)
				for key in (USER, frappe.local.request_ip):
					tracker(key, raise_locked_exception=False).add_success_attempt()
				account.confirm_password(USER, "alice-passport-1")
				check("correct password works after lockout is cleared", True)
		finally:
			for field in ("login_failed_count", "login_failed_time"):
				for key in (USER, frappe.local.request_ip):
					frappe.cache.hdel(field, prefix + key)

	# Background disable/re-enable must invalidate ordinary issuer API access too.
	for cached in (True, False):
		browser = sessions.browser()
		assert browser.post(ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"}).ok
		sid = browser.cookies.get("sid", domain="id.localhost")
		with db("id.localhost"):
			try:
				doc = frappe.get_doc("User", USER)
				doc.enabled = 0
				doc.save(ignore_permissions=True)
				frappe.db.commit()
			finally:
				doc = frappe.get_doc("User", USER)
				doc.enabled = 1
				doc.save(ignore_permissions=True)
			if not cached:
				frappe.cache.hdel("session", sid)
		response = browser.get(ID + "/api/method/frappe.auth.get_logged_user")
		check(
			"issuer revoked cookie denied ordinary API (cache=" + str(cached) + ")",
			response.status_code in (401, 403),
		)
		check("issuer revoked cookie reaches login", browser.get(ID + "/login").ok)
		check(
			"issuer fresh password login succeeds",
			browser.post(ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"}).ok,
		)
