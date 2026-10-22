"""Live browser binding, callback replay, PKCE and redirect checks."""

import re
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from live_support import ID, SITE, USER, check, db, get
from passport_client import auth, protocol, store


def pending_login():
	browser = requests.Session()
	response = browser.get(SITE + "/passport/login", timeout=10)
	authorize_path = parse_qs(urlparse(response.url).query)["redirect-to"][0]
	assert browser.post(
		ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"}, timeout=10
	).ok
	response = browser.get(ID + authorize_path, allow_redirects=False, timeout=10)
	assert response.status_code == 302
	return browser, response.headers["Location"]


browser, callback = pending_login()
check("callback is bound to initiating browser", requests.get(callback, timeout=10).status_code == 403)
check(
	"rejected foreign browser does not consume legitimate attempt",
	browser.get(callback, timeout=10).ok and get(browser).ok,
)
csrf = re.search(r'csrf_token = "([^"]+)"', browser.get(SITE + "/desk", timeout=10).text).group(1)
assert browser.post(SITE + "/api/method/logout", headers={"X-Frappe-CSRF-Token": csrf}, timeout=10).ok
check("callback cannot be replayed after local logout", browser.get(callback, timeout=10).status_code == 403)
check("replay leaves no authenticated site session", not get(browser).ok)

browser, callback = pending_login()
parameters = parse_qs(urlparse(callback).query)
with db():
	attempt = store.get("attempt", protocol.digest(parameters["state"][0].encode()))
	body = {
		"code": parameters["code"][0],
		"redirect_uri": SITE + "/passport/callback",
		"code_verifier": "wrong",
	}
	try:
		auth.call("/oidc/token", body)
		raise AssertionError("Wrong PKCE verifier accepted")
	except auth.PassportError:
		check("wrong PKCE verifier rejected", True)
	body["code_verifier"] = attempt["verifier"]
	result = auth.call("/oidc/token", body)
	check("token response contains only an ID token", set(result) == {"id_token"})
	try:
		auth.call("/oidc/token", body)
		raise AssertionError("Authorization code reused")
	except auth.PassportError:
		check("authorization code is single-use", True)
check(
	"spent code cannot create client session",
	browser.get(callback, timeout=10).status_code == 403 and not get(browser).ok,
)

for target in ("https://example.com", "//example.com", "/\\example.com"):
	response = requests.get(
		SITE + "/passport/login?" + urlencode({"redirect-to": target}), allow_redirects=False, timeout=10
	)
	state = parse_qs(urlparse(response.headers["Location"]).query)["state"][0]
	with db():
		attempt = store.get("attempt", protocol.digest(state.encode()))
		check("unsafe return destination replaced", attempt["return"] == "/desk")
