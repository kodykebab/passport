"""Frappe ID browser login and server-to-server authenticated exchange."""

import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import frappe
import jwt
import requests
from passport_core.ui import assets, render_page
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport_client import protocol, store


class PassportError(Exception):
	pass


def config():
	c = frappe.conf
	if not (c.passport_issuer and c.passport_client_id and c.passport_private_key and c.host_name):
		return None
	return frappe._dict(
		issuer=c.passport_issuer,
		client_id=c.passport_client_id,
		private_key=c.passport_private_key,
		kid=c.passport_key_id,
		redirect_uri=c.host_name + "/passport/callback",
	)


def safe_redirect(path):
	return (
		path
		if path
		and path.startswith("/")
		and not path.startswith("//")
		and "\\" not in path
		and not any(ord(c) < 32 for c in path)
		else "/desk"
	)


def call(path, body):
	c = config()
	body = json.dumps({**body, "client_id": c.client_id}, separators=(",", ":")).encode()
	endpoint = c.issuer + path
	try:
		r = requests.post(
			endpoint,
			data=body,
			headers={
				"Content-Type": "application/json",
				"Passport-Client-Assertion": protocol.assertion(
					c.client_id, endpoint, body, c.private_key, c.kid
				),
			},
			timeout=5,
			allow_redirects=False,
		)
		r.raise_for_status()
		return r.json()
	except (requests.RequestException, ValueError) as e:
		raise PassportError("Frappe ID could not validate this session. Retry when it is available.") from e


def verify(token, purpose, required):
	c = config()
	# Configured issuer only. No URL from a token or discovery document is followed.
	key = frappe.cache.get_value("passport:issuer-jwk")
	header = jwt.get_unverified_header(token)
	if key is None or key.get("kid") != header.get("kid"):
		if not frappe.cache.set(frappe.cache.make_key("passport:jwks-refresh"), "1", nx=True, ex=5):
			raise PassportError("Signing key refresh temporarily unavailable.")
		r = requests.get(c.issuer + "/passport/jwks", timeout=5, allow_redirects=False)
		r.raise_for_status()
		keys = r.json()["keys"]
		key = next(k for k in keys if k["kid"] == header.get("kid"))
		frappe.cache.set_value("passport:issuer-jwk", key, expires_in_sec=300)
	return protocol.verify(token, key, purpose, c.issuer, c.client_id, required)


def login():
	c = config()
	if not c:
		raise PassportError("Frappe ID is not configured.")
	if frappe.session.user != "Guest":
		return redirect(safe_redirect(frappe.request.args.get("redirect-to")))
	return start_authorization()


def start_authorization(context=None):
	c = config()
	state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
	browser = frappe.request.cookies.get("passport_browser") or secrets.token_urlsafe(32)
	store.create(
		"attempt",
		protocol.digest(state.encode()),
		{
			**(context or {}),
			"nonce": nonce,
			"verifier": verifier,
			"browser": protocol.digest(browser.encode()),
			"return": safe_redirect(frappe.request.args.get("redirect-to")),
			"exp": time.time() + 600,
		},
	)
	frappe.db.commit()
	query = {
		"response_type": "code",
		"client_id": c.client_id,
		"redirect_uri": c.redirect_uri,
		"scope": "openid email profile",
		"state": state,
		"nonce": nonce,
		"code_challenge": protocol.pkce_challenge(verifier),
		"code_challenge_method": "S256",
	}
	if context and context.get("action") == "link":
		query["passport_action"] = "link"
	r = redirect(c.issuer + "/oidc/authorize?" + urlencode(query))
	r.set_cookie(
		"passport_browser",
		browser,
		max_age=600,
		httponly=True,
		samesite="Lax",
		secure=frappe.request.scheme == "https",
	)
	return r


def resolve_user(issuer, claims):
	user = frappe.db.get_value("Passport Identity", {"issuer": issuer, "subject": claims["sub"]}, "user")
	if not user or user in ("Guest", "Administrator") or not frappe.db.get_value("User", user, "enabled"):
		raise PassportError(
			"This identity has no enabled account on this site. Ask the site administrator for access."
		)
	# login_as bypasses the password/MFA flow, so refuse stronger local policy.
	if frappe.get_system_settings("enable_two_factor_auth"):
		raise PassportError("Use this site's local login to satisfy its authentication policy.")
	return user


def callback():
	key = protocol.digest(frappe.request.args.get("state", "").encode())
	attempt = store.get("attempt", key, lock=True)
	browser = protocol.digest(frappe.request.cookies.get("passport_browser", "").encode())
	if (
		not attempt
		or attempt.get("spent")
		or attempt["exp"] <= time.time()
		or not hmac.compare_digest(attempt["browser"], browser)
	):
		raise PassportError("This login attempt has expired or belongs to another browser.")
	linking = attempt.get("action") == "link"
	if linking:
		if frappe.session.user != attempt["user"] or frappe.session.sid != attempt["local_sid"]:
			raise PassportError("The local session changed. Start linking again.")
	elif frappe.session.user != "Guest":
		raise PassportError("A local session is already active. Log out before starting another login.")
	attempt["spent"] = True
	store.put("attempt", key, attempt)
	frappe.db.commit()
	if frappe.request.args.get("error"):
		raise PassportError(
			"Linking was cancelled. No account link was created." if linking else "Sign-in was cancelled."
		)
	c = config()
	result = call(
		"/oidc/token",
		{
			"code": frappe.request.args.get("code", ""),
			"redirect_uri": c.redirect_uri,
			"code_verifier": attempt["verifier"],
		},
	)
	claims = verify(
		result["id_token"],
		"passport-link+jwt" if linking else "passport-id+jwt",
		["sub", "sid", "nonce", "auth_time", "amr", "passport_auth_method", "issuer_session_expires_at"],
	)
	if (
		claims["nonce"] != attempt["nonce"]
		or (claims["amr"], claims.get("passport_auth_method"))
		not in ((["pwd"], "password"), (["otp"], "email_code"))
		or claims["exp"] - claims["iat"] > 60
	):
		raise PassportError("Invalid login evidence.")
	if linking:
		from passport_client.linking import finish

		return finish(attempt, claims)
	user = resolve_user(c.issuer, claims)
	binding = secrets.token_hex(24)
	deadline = claims["issuer_session_expires_at"]
	frappe.flags.passport_pending = {
		"protocol_sid": claims["sid"],
		"binding": binding,
		"user": user,
		"sub": claims["sub"],
		"issuer": c.issuer,
		"deadline": deadline,
		"exp": 0,
		"revoked": False,
		"revision": 0,
	}
	frappe.local.login_manager.login_as(
		user, session_end=datetime.fromtimestamp(deadline, timezone.utc).isoformat()
	)
	frappe.db.commit()
	from passport_client.federation import renew

	try:
		renew(complete=True)
	except Exception:
		# A failed completion must not leave a pending cookie that blocks login.
		from frappe.auth import clear_cookies
		from frappe.sessions import delete_session

		delete_session(frappe.session.sid, user=user, reason="Frappe ID completion failed")
		clear_cookies()
		raise
	return redirect(attempt["return"])


def session_page(title, message, link, link_label, *, status=200, csrf_token=None):
	html = render_page(
		"passport_client",
		"Frappe ID",
		{
			"page": "session",
			"title": title,
			"message": message,
			"link": link,
			"link_label": link_label,
			"csrf_token": csrf_token,
		},
	)
	return Response(html, status=status, content_type="text/html", headers={"Cache-Control": "no-store"})


def response():
	path = frappe.request.path
	if path not in (
		"/passport/login",
		"/passport/callback",
		"/passport/retry",
		"/passport/status",
		"/passport/link",
	):
		return None
	if frappe.request.method not in (
		("GET", "POST") if path == "/passport/link" else ("POST" if path == "/passport/retry" else "GET",)
	):
		return Response(status=405)
	try:
		if path == "/passport/link":
			from passport_client.linking import response as link_response

			result = link_response()
			result.headers["Cache-Control"] = "no-store"
			result.headers["Referrer-Policy"] = "no-referrer"
			return result
		if path == "/passport/status":
			from frappe.sessions import get_csrf_token

			return session_page(
				"Validate session",
				"Validation does not replay interrupted actions.",
				"/desk",
				"Return to site",
				csrf_token=get_csrf_token(),
			)
		if path == "/passport/retry":
			from passport_client.federation import renew

			expires = renew()
			if frappe.request.form.get("response_format") == "html":
				result = redirect("/desk", code=303)
				result.headers["Cache-Control"] = "no-store"
				return result
			return Response(
				json.dumps({"ok": True, "expires": expires}),
				content_type="application/json",
				headers={"Cache-Control": "no-store"},
			)
		result = login() if path == "/passport/login" else callback()
		result.headers["Cache-Control"] = "no-store"
		result.headers["Referrer-Policy"] = "no-referrer"
		return result
	except (
		PassportError,
		requests.RequestException,
		jwt.InvalidTokenError,
		ValueError,
		KeyError,
		StopIteration,
	) as e:
		frappe.db.rollback()
		# Never log assertion/token/callback query contents.
		message = (
			str(e)
			if isinstance(e, PassportError)
			else "Could not complete Frappe ID validation. Start a new login."
		)
		return session_page("Login failed", message, "/login", "Back to login", status=403)


def update_login_context(context):
	if frappe.request.path != "/login" or not config():
		return
	context.setdefault("provider_logins", []).append(
		{"name": "passport", "provider_name": "Frappe ID", "auth_url": "/passport/login", "icon": ""}
	)
	context["social_login"] = True


def extend_bootinfo(bootinfo):
	if frappe.conf.get("passport_allow_linking"):
		bootinfo.passport_link_url = "/passport/link"
	row = store.get("federation", frappe.session.sid)
	if row and not row["revoked"]:
		bootinfo.passport = {
			"account_url": config().issuer + "/account",
			"sites_url": config().issuer + "/sites",
			"expires": row["exp"],
			"ui": assets("passport_client"),
		}
