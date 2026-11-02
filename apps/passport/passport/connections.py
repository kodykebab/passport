"""Issuer-side site authorization; callers lock the User before mutating or checking."""

from urllib.parse import urlsplit

import frappe
from passport_core.ui import render_page
from werkzeug.wrappers import Response

from passport import protocol, registration, store


def report(client, body):
	if (
		set(body) != {"client_id", "subject", "installation", "sequence", "allowed"}
		or not isinstance(body["subject"], str)
		or not body["subject"]
		or len(body["subject"]) > 140
		or type(body["sequence"]) is not int
		or not 0 < body["sequence"] < 2**63
		or type(body["allowed"]) is not bool
		or body["installation"] != client.registration_revision
		or not body["installation"]
	):
		raise ValueError("invalid_outcome")
	user = frappe.db.get_value("Passport Account", body["subject"], "user")
	if not user:
		raise ValueError("unknown_subject")
	frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", user)
	account = frappe.db.sql(
		"SELECT user FROM `tabPassport Account` WHERE name=%s FOR UPDATE", body["subject"]
	)
	if not account or account[0][0] != user:
		raise ValueError("subject_changed")
	identifier = key(user, client.name)
	row = store.get("connection", identifier, lock=True) or {
		"user": user,
		"client": client.name,
		"generation": 0,
		"revoked": False,
	}
	registration.check(client.name, client.registration_revision)
	sequence = (
		row.get("admission_sequence", 0) if row.get("admission_installation") == body["installation"] else 0
	)
	if sequence == body["sequence"] and row["admission_hidden"] == body["allowed"]:
		raise ValueError("conflicting_outcome")
	if body["sequence"] > sequence:
		row.update(
			admission_installation=body["installation"],
			admission_sequence=body["sequence"],
			admission_hidden=not body["allowed"],
		)
		store.put("connection", identifier, row)
	return {"installation": body["installation"], "sequence": body["sequence"]}


def key(user, client):
	return protocol.digest((user + "\0" + client).encode())


def check(user, client, expected=None):
	row = store.get("connection", key(user, client), lock=True) or {}
	generation = row.get("generation", 0)
	if row.get("revoked") or (expected is not None and expected != generation):
		raise ValueError("site_access_revoked")
	return generation


def remember(user, client):
	identifier = key(user, client)
	if not store.get("connection", identifier, lock=True):
		store.put(
			"connection", identifier, {"user": user, "client": client, "generation": 0, "revoked": False}
		)


def site_origin(callback):
	if (
		not isinstance(callback, str)
		or "\\" in callback
		or any(ord(c) <= 32 or ord(c) == 127 for c in callback)
	):
		return None
	try:
		url = urlsplit(callback)
		if (
			url.scheme not in ("http", "https")
			or not url.hostname
			or url.username is not None
			or url.password is not None
			or url.path != "/passport/callback"
			or url.query
			or url.fragment
		):
			return None
		url.port
		return f"{url.scheme}://{url.netloc}"
	except ValueError:
		return None


def sites(user, *, picker=False):
	known = {row["client"]: {"revoked": False} for _, row in store.rows("login", user=user)}
	known.update({row["client"]: row for _, row in store.rows("connection", user=user)})
	result = []
	for client, row in sorted(known.items()):
		registered = frappe.db.get_value(
			"Passport Client",
			client,
			["title", "redirect_uri", "enabled", "registration_revision"],
			as_dict=True,
		)
		hidden = row.get("admission_hidden", False) or bool(
			row.get("admission_installation")
			and registered
			and row["admission_installation"] != registered.registration_revision
		)
		if picker and hidden:
			continue
		origin = site_origin(registered.redirect_uri) if registered else None
		result.append(
			{
				"client": client,
				"title": registered.title if registered else client,
				"origin": origin or "Registration unavailable",
				"login_url": origin + "/passport/login"
				if origin and registered.enabled and not row["revoked"] and not hidden
				else None,
				"revoked": row["revoked"],
			}
		)
	return result


def set_revoked(user, client, revoked):
	identifier = key(user, client)
	row = store.get("connection", identifier, lock=True)
	if not row:
		if not any(record["client"] == client for _, record in store.rows("login", user=user, lock=True)):
			raise ValueError("Unknown connected site")
		row = {"user": user, "client": client, "generation": 0, "revoked": False}
	if revoked and not row["revoked"]:
		row["generation"] += 1
	row["revoked"] = revoked
	store.put("connection", identifier, row)


def revoked_page():
	return Response(
		render_page(
			"passport",
			"Site access revoked",
			{
				"page": "session",
				"title": "Site access revoked",
				"message": "You revoked access for this site. To sign in again, open your Frappe ID account and allow future sign-ins, then start a new login from the site.",
				"link": "/account",
				"link_label": "Manage connected sites",
			},
		),
		status=403,
		content_type="text/html",
	)
