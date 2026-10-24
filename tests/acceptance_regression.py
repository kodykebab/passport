"""HTTP acceptance regressions; cleans up only sessions created by this test."""

import time

import frappe
from frappe.sessions import delete_session
from live_support import ID, SITE, USER, check, db, get, login, renew, store
from page_data import page_data

sessions = []


def new_login():
	browser, csrf, sid = login()
	sessions.append(("site1.localhost", sid))
	sessions.append(("id.localhost", browser.cookies.get("sid", domain="id.localhost")))
	return browser, csrf, sid


def csrf_from(page):
	return page_data(page.text)["csrf_token"]


def expire(sid):
	with db():
		row = store.get("federation", sid)
		row["exp"] = int(time.time()) - 1
		store.put("federation", sid, row)


try:
	browser, csrf, sid = new_login()
	issuer_sid = browser.cookies.get("sid", domain="id.localhost")
	page = browser.get(ID + "/account")
	token = csrf_from(page)
	with db("id.localhost"):
		name = frappe.db.get_value("User", USER, "full_name")
	with db():
		local_name = frappe.db.get_value("User", USER, "full_name")
	for password in ("incorrect-acceptance-password", ""):
		response = browser.post(
			ID + "/account",
			data={
				"action": "name",
				"display_name": "Must not apply",
				"password": password,
				"csrf_token": token,
			},
		)
		check(
			"password rejection is inline without traceback",
			response.status_code == 403
			and "Incorrect password" in response.text
			and "Traceback" not in response.text
			and "Session Expired" not in response.text,
		)
		check(
			"password rejection preserves issuer cookie",
			browser.cookies.get("sid", domain="id.localhost") == issuer_sid,
		)
		check(
			"account remains accessible after rejection",
			browser.get(ID + "/account", allow_redirects=False).status_code == 200,
		)
		with db("id.localhost"):
			check(
				"rejected edit leaves display name unchanged",
				frappe.db.get_value("User", USER, "full_name") == name,
			)
	response = browser.post(
		ID + "/account",
		data={"action": "name", "display_name": name, "password": "alice-passport-1", "csrf_token": token},
		allow_redirects=False,
	)
	check("correct password works in the same issuer session", response.status_code == 302)
	with db():
		check("site-owned name unchanged", frappe.db.get_value("User", USER, "full_name") == local_name)

	expire(sid)
	check("expired lease blocks Desk", browser.get(SITE + "/desk").status_code == 403)
	page = browser.get(SITE + "/passport/status")
	check("recovery page provides CSRF", bool(page_data(page.text)["csrf_token"]))
	response = browser.post(
		SITE + "/passport/retry",
		data={"csrf_token": csrf_from(page), "response_format": "html"},
		allow_redirects=False,
	)
	check(
		"form renewal redirects with GET semantics",
		response.status_code == 303 and response.headers["Location"] == "/desk",
	)
	check("Desk works after form renewal", browser.get(SITE + response.headers["Location"]).ok)
	response = renew(browser, csrf)
	check(
		"automatic renewal still returns JSON",
		response.ok and response.json()["ok"] is True and "expires" in response.json(),
	)

	expire(sid)
	check(
		"expired local logout succeeds",
		browser.post(SITE + "/api/method/logout", headers={"X-Frappe-CSRF-Token": csrf}).ok,
	)
	check("expired local logout removes access", not get(browser).ok)
	fresh, _, _ = new_login()
	check("fresh password login succeeds after expired logout", get(fresh).ok)
finally:
	for site, sid in sessions:
		with db(site):
			if frappe.db.exists("Sessions", {"sid": sid}):
				delete_session(sid, user=USER, reason="Acceptance regression cleanup")
print("PASS dedicated regression sessions cleaned up")
