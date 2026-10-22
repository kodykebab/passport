"""Dedicated Frappe ID code/PKCE and renewable-lease protocol."""

import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import frappe
import jwt
from werkzeug.exceptions import BadRequest
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport import protocol, store


def issuer():
	return frappe.conf.passport_issuer


def json_response(status, body):
	return Response(
		json.dumps(body),
		status=status,
		content_type="application/json",
		headers={"Cache-Control": "no-store"},
	)


def get_subject(user):
	if subject := frappe.db.get_value("Passport Account", {"user": user}):
		return subject
	return frappe.get_doc({"doctype": "Passport Account", "user": user}).insert(ignore_permissions=True).name


def signing(claims, purpose):
	return protocol.sign(claims, frappe.conf.passport_signing_key, frappe.conf.passport_signing_kid, purpose)


def jwks():
	return json_response(
		200,
		{"keys": [protocol.public_jwk(frappe.conf.passport_signing_key, frappe.conf.passport_signing_kid)]},
	)


def discovery():
	return json_response(
		200,
		{
			"issuer": issuer(),
			"jwks_uri": issuer() + "/passport/jwks",
			"authorization_endpoint": issuer() + "/oidc/authorize",
			"token_endpoint": issuer() + "/oidc/token",
			"passport_profile": 1,
			"response_types_supported": ["code"],
			"code_challenge_methods_supported": ["S256"],
			"id_token_signing_alg_values_supported": ["EdDSA"],
			"passport_client_authentication": "body-bound-ed25519",
		},
	)


def boundary():
	path = frappe.request.path
	if path in ROUTES:
		if (
			"cmd" in frappe.form_dict
			or "cmd" in frappe.request.args
			or "cmd" in frappe.request.form
			or "sid" in frappe.request.args
			or "sid" in frappe.request.form
			or frappe.request.headers.get("Authorization")
		):
			raise BadRequest("Invalid protocol parameters")
		if any(len(frappe.request.args.getlist(k)) != 1 for k in frappe.request.args):
			raise BadRequest("Duplicate parameters")
		if path in BACKCHANNEL:
			if (
				frappe.request.cookies.get("sid")
				or frappe.request.content_length is None
				or frappe.request.content_length > 16384
			):
				raise BadRequest("Invalid protocol request")
			frappe.flags.passport_background = True
			frappe.form_dict.pop("sid", None)


def authenticate():
	raw = frappe.request.get_data()
	if frappe.request.mimetype != "application/json":
		raise ValueError("JSON required")
	body = json.loads(raw)
	if not isinstance(body, dict):
		raise ValueError("JSON object required")
	for field in ("client_id", "code", "code_verifier", "redirect_uri", "sid", "binding", "operation"):
		if field in body and not isinstance(body[field], str):
			raise ValueError("Invalid protocol field")
	if "deadline" in body and type(body["deadline"]) is not int:
		raise ValueError("Invalid deadline")
	client_id = body.get("client_id")
	client = frappe.db.get_value(
		"Passport Client",
		{"name": client_id, "enabled": 1},
		["name", "public_jwk", "redirect_uri"],
		as_dict=True,
	)
	if not client:
		raise ValueError("Unknown client")
	claims = protocol.verify(
		frappe.request.headers.get("Passport-Client-Assertion", ""),
		json.loads(client.public_jwk),
		"passport-client+jwt",
		client_id,
		issuer() + frappe.request.path,
		["sub", "jti", "body_sha256"],
	)
	if (
		claims["sub"] != client_id
		or claims["exp"] - claims["iat"] > 60
		or not hmac.compare_digest(claims["body_sha256"], protocol.digest(raw))
	):
		raise ValueError("Invalid assertion")
	store.create("replay", protocol.digest((client_id + claims["jti"]).encode()), {"exp": claims["exp"]})
	return client, body


def authorize():
	a = frappe.request.args
	client = frappe.db.get_value(
		"Passport Client", {"name": a.get("client_id"), "enabled": 1}, ["name", "redirect_uri"], as_dict=True
	)
	if not client or a.get("redirect_uri") != client.redirect_uri:
		return json_response(400, {"error": "invalid_request"})
	if (
		a.get("response_type") != "code"
		or a.get("code_challenge_method") != "S256"
		or not all(a.get(k) for k in ("nonce", "state", "code_challenge"))
	):
		return json_response(400, {"error": "invalid_request"})
	if frappe.session.user == "Guest":
		return redirect("/login?" + urlencode({"redirect-to": frappe.request.full_path}))
	if frappe.session.user == "Administrator":
		return json_response(403, {"error": "access_denied"})
	native = store.native(frappe.session.sid, frappe.session.user, lock=True)
	if not native:
		return json_response(403, {"error": "login_revoked"})
	code = secrets.token_urlsafe(32)
	store.create(
		"grant",
		protocol.digest(code.encode()),
		{
			"client": client.name,
			"redirect_uri": client.redirect_uri,
			"user": frappe.session.user,
			"sub": get_subject(frappe.session.user),
			"issuer_sid": frappe.session.sid,
			"nonce": a["nonce"],
			"challenge": a["code_challenge"],
			"exp": time.time() + 60,
		},
	)
	frappe.db.commit()
	return redirect(client.redirect_uri + "?" + urlencode({"code": code, "state": a["state"]}))


def token(client, body):
	key = protocol.digest(body.get("code", "").encode())
	grant = store.get("grant", key, lock=True)
	if (
		not grant
		or grant.get("spent")
		or grant["exp"] <= time.time()
		or grant["client"] != client.name
		or grant["redirect_uri"] != body.get("redirect_uri")
		or not hmac.compare_digest(grant["challenge"], protocol.pkce_challenge(body.get("code_verifier", "")))
	):
		raise ValueError("invalid_grant")
	native = store.native(grant["issuer_sid"], grant["user"], lock=True)
	if not native:
		raise ValueError("login_revoked")
	now = int(time.time())
	from datetime import datetime

	deadline = int(datetime.fromisoformat(native["session_end"]).timestamp())
	sid = secrets.token_hex(24)
	grant["spent"] = True
	store.put("grant", key, grant)
	store.create("login", sid, {**grant, "deadline": deadline, "pending_until": now + 120, "revoked": False})
	user = frappe.db.get_value("User", grant["user"], ["email", "full_name"], as_dict=True)
	claims = {
		"iss": issuer(),
		"aud": client.name,
		"sub": grant["sub"],
		"iat": now,
		"exp": now + 60,
		"sid": sid,
		"nonce": grant["nonce"],
		"email": user.email,
		"name": user.full_name,
		"amr": ["pwd"],
		"auth_time": native.get("passport_auth_time"),
		"issuer_session_expires_at": deadline,
	}
	return {"id_token": signing(claims, "passport-id+jwt")}


def lease(client, body):
	# Native validation locks User then Sessions before the login record.
	login = store.get("login", body.get("sid", ""))
	if (
		not login
		or login["client"] != client.name
		or not store.native(login["issuer_sid"], login["user"], lock=True)
	):
		raise ValueError("login_revoked")
	login = store.get("login", body["sid"], lock=True)
	if login["revoked"] or login["deadline"] <= time.time():
		raise ValueError("login_revoked")
	if "binding" not in login:
		if frappe.request.path != "/passport/complete" or login["pending_until"] <= time.time():
			raise ValueError("invalid_grant")
		login["binding"] = body["binding"]
		login["deadline"] = min(login["deadline"], int(body["deadline"]))
	elif login["binding"] != body["binding"]:
		raise ValueError("invalid_grant")
	now = int(time.time())
	claims = {
		"iss": issuer(),
		"aud": client.name,
		"sub": login["sub"],
		"sid": body["sid"],
		"binding": login["binding"],
		"operation": body["operation"],
		"iat": now,
		"exp": min(now + 300, login["deadline"]),
	}
	store.put("login", body["sid"], login)
	return {"lease": signing(claims, "passport-lease+jwt")}


BACKCHANNEL = {"/oidc/token": token, "/passport/complete": lease, "/passport/renew": lease}
ROUTES = {
	"/.well-known/openid-configuration": ("GET", discovery),
	"/passport/jwks": ("GET", jwks),
	"/oidc/authorize": ("GET", authorize),
	**{p: ("POST", None) for p in BACKCHANNEL},
}


def response():
	if frappe.request.path not in ROUTES:
		return None
	method, handler = ROUTES[frappe.request.path]
	if frappe.request.method != method:
		return json_response(405, {"error": "invalid_method"})
	if handler:
		result = handler()
		result.headers["Cache-Control"] = "no-store"
		result.headers["Referrer-Policy"] = "no-referrer"
		return result
	try:
		client, body = authenticate()
	except ValueError, TypeError, KeyError, jwt.InvalidTokenError:
		frappe.db.rollback()
		return json_response(401, {"error": "invalid_client"})
	# Commit replay protection even if the operation fails.
	frappe.db.commit()
	try:
		result = BACKCHANNEL[frappe.request.path](client, body)
		frappe.db.commit()
		return json_response(200, result)
	except ValueError, KeyError, TypeError:
		frappe.db.rollback()
		return json_response(403, {"error": "invalid_grant"})
