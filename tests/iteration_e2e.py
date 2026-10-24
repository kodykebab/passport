"""Live one-issuer/one-client regression checks. Existing demo identity required."""

import re
from urllib.parse import parse_qs, urlparse

import requests
from live_support import BrowserSessions
from page_data import page_data

ID = "http://id.localhost:8080"
SITE = "http://site1.localhost:8080"
USER = "alice@example.com"
with BrowserSessions() as sessions:
	browser = sessions.browser()

	def check(label, value):
		assert value, label
		print("PASS", label)

	def who(site):
		r = browser.get(site + "/api/method/frappe.auth.get_logged_user")
		return r.json().get("message") if r.ok else None

	r = browser.get(ID + "/.well-known/openid-configuration")
	check("discovery", r.ok and r.json()["passport_profile"] == 1)
	r = browser.get(ID + "/passport/jwks")
	check(
		"Ed25519 public JWKS",
		r.ok and r.json()["keys"][0]["crv"] == "Ed25519" and "d" not in r.json()["keys"][0],
	)
	r = browser.get(SITE + "/passport/login")
	check("redirect to issuer password login", r.url.startswith(ID + "/login"))
	return_path = parse_qs(urlparse(r.url).query)["redirect-to"][0]
	r = browser.post(ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"})
	check("password authentication", r.ok)
	r = browser.get(ID + return_path)
	check("existing linked identity logs in", who(SITE) == USER)
	r = browser.get(SITE + "/desk")
	csrf = re.search(r'csrf_token = "([^"]+)"', r.text).group(1)
	r = browser.post(SITE + "/passport/retry", headers={"X-Frappe-CSRF-Token": csrf})
	check("authenticated renewal", r.ok)
	r = browser.get(ID + "/account")
	check("account page and read-only email", r.ok and page_data(r.text)["profile"]["email"] == USER)
	issuer_csrf = page_data(r.text)["csrf_token"]
	r = browser.post(
		ID + "/account",
		data={"action": "name", "display_name": "Alice", "password": "wrong", "csrf_token": issuer_csrf},
	)
	check("fresh password required", not r.ok)
	r = browser.post(
		ID + "/account",
		data={
			"action": "name",
			"display_name": "Alice",
			"password": "alice-passport-1",
			"csrf_token": issuer_csrf,
		},
	)
	check("display name update", r.ok)
	r = browser.post(
		ID + "/account",
		data={"action": "everywhere", "password": "alice-passport-1", "csrf_token": issuer_csrf},
		allow_redirects=False,
	)
	check("logout everywhere accepted", r.status_code == 302)
	r = browser.post(SITE + "/passport/retry", headers={"X-Frappe-CSRF-Token": csrf})
	check("issuer logout blocks renewal", not r.ok)
	r = browser.post(SITE + "/api/method/logout", headers={"X-Frappe-CSRF-Token": csrf})
	check("local logout works after issuer revocation", r.ok and who(SITE) != USER)
	check(
		"shared secret rejected",
		requests.post(
			ID + "/oidc/token", json={"client_id": "site1.localhost", "client_secret": "old"}
		).status_code
		== 401,
	)
	print("All one-client smoke checks passed")
