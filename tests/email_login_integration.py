"""Live issuer email delivery, browser binding, authentication context and cleanup."""

import re
import time
from urllib.parse import parse_qs, urlparse

import frappe
import requests
from email_forms import Forms
from live_support import ID, SITE, USER, BrowserSessions, check, db, get, store
from page_data import page_data

MAIL = "http://127.0.0.1:8025"


with BrowserSessions() as sessions:
	browser = sessions.browser()
	attempts, messages = [], []

	def inbox_code(attempt):
		for _ in range(40):
			for message in requests.get(MAIL + "/api/v1/messages", timeout=5).json()["messages"]:
				body = requests.get(MAIL + "/api/v1/message/" + message["ID"], timeout=5).json()
				if attempt[:8] in body["Text"]:
					messages.append(message["ID"])
					return re.search(r"\b[0-9]{6}\b", body["Text"]).group()
			time.sleep(0.5)
		raise AssertionError("Code did not arrive through the queue and SMTP catcher")

	try:
		start = browser.get(SITE + "/passport/login", timeout=10)
		authorize = parse_qs(urlparse(start.url).query)["redirect-to"][0]
		page = browser.get(ID + "/passport/email", params={"redirect-to": authorize}, timeout=10)
		form = Forms(page.text).action("send")
		attempts.append(form["attempt"])
		response = browser.post(ID + "/passport/email", data={**form, "email": USER}, timeout=10)
		check(
			"code request accepted without revealing account status",
			response.ok and bool(page_data(response.text)["challenge"]),
		)
		code = inbox_code(form["attempt"])
		check("code delivered by Frappe queue to local SMTP", bool(code))
		verification = Forms(response.text).action("verify")
		with db("id.localhost"):
			row = store.get("email_challenge", form["attempt"])
			check("challenge stores no plaintext code", code not in row.values())
		check(
			"attempt identifier and code cannot authenticate another browser",
			requests.post(ID + "/passport/email", data={**verification, "code": code}, timeout=10).status_code
			== 403,
		)
		bad = browser.post(
			ID + "/passport/email", data={**verification, "form_token": "wrong", "code": code}, timeout=10
		)
		check("email form rejects missing browser CSRF proof", bad.status_code == 403)
		wrong = "000000" if code != "000000" else "111111"
		response = browser.post(ID + "/passport/email", data={**verification, "code": wrong}, timeout=10)
		check(
			"incorrect code leaves a usable verification page",
			response.status_code == 400 and "Incorrect" in response.text and "Traceback" not in response.text,
		)
		response = browser.post(
			ID + "/passport/email", data={**verification, "code": code}, allow_redirects=False, timeout=10
		)
		check(
			"successful email verification resumes original authorization",
			response.status_code == 303 and response.headers["Location"] == authorize,
		)
		issuer_sid = browser.cookies.get("sid", domain="id.localhost")
		response = browser.get(ID + authorize, timeout=10)
		check(
			"email identity reaches authenticated client",
			response.ok and get(browser).json().get("message") == USER,
		)
		with db("id.localhost"):
			native = store.get_valid_native_session(issuer_sid)
			check(
				"issuer session records actual authentication method",
				native["passport_auth_method"] == "email_code",
			)
			check(
				"verified challenge is durably consumed",
				store.get("email_challenge", form["attempt"])["spent"],
			)
		page = browser.get(ID + "/account", timeout=10)
		csrf = page_data(page.text)["csrf_token"]
		response = browser.post(
			ID + "/account",
			data={"action": "name", "display_name": "Not applied", "password": code, "csrf_token": csrf},
			timeout=10,
		)
		check(
			"email login does not replace fresh account password confirmation",
			response.status_code == 403 and "Incorrect password" in response.text,
		)

		unknown = sessions.browser()
		page = unknown.get(ID + "/passport/email", timeout=10)
		form = Forms(page.text).action("send")
		attempts.append(form["attempt"])
		response = unknown.post(
			ID + "/passport/email", data={**form, "email": "nonexistent-email-test@example.com"}, timeout=10
		)
		check(
			"unknown account gets the same public request screen",
			response.ok and bool(page_data(response.text)["challenge"]),
		)
		with db("id.localhost"):
			check(
				"unknown account is not created or mailed",
				not frappe.db.exists("User", "nonexistent-email-test@example.com")
				and not store.get("email_challenge", form["attempt"])["mail"],
			)
	finally:
		with db("id.localhost"):
			for attempt in attempts:
				row = store.get("email_challenge", attempt)
				if row:
					for name in row["mail"]:
						frappe.db.delete("Email Queue Recipient", {"parent": name})
						frappe.db.delete("Email Queue", {"name": name})
					frappe.db.sql(
						"DELETE FROM passport_state WHERE kind='email_challenge' AND id=%s", attempt
					)
		for message in set(messages):
			requests.delete(MAIL + "/api/v1/messages", json={"IDs": [message]}, timeout=5)
	print("PASS dedicated email challenges, messages and sessions cleaned up")
