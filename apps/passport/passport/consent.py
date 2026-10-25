"""One-use issuer confirmation for an existing-account linking request."""

import hmac
import secrets
import time
from urllib.parse import urlencode, urlsplit

import frappe
from frappe.sessions import get_csrf_token
from passport_core.ui import render_page
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport import connections, store

PATH = "/passport/consent"


def page(client, args):
	request_id = secrets.token_urlsafe(32)
	store.create(
		"link_consent",
		request_id,
		{
			"user": frappe.session.user,
			"issuer_sid": frappe.session.sid,
			"client": client.name,
			"connection_generation": connections.check(frappe.session.user, client.name),
			"args": dict(args),
			"exp": time.time() + 600,
		},
	)
	frappe.db.commit()
	profile = frappe.db.get_value("User", frappe.session.user, ["email", "full_name"], as_dict=True)
	origin = urlsplit(client.redirect_uri)
	return Response(
		render_page(
			"passport",
			"Link your Frappe ID",
			{
				"page": "consent",
				"title": "Link your Frappe ID",
				"request_id": request_id,
				"csrf_token": get_csrf_token(),
				"client_title": client.title,
				"origin": f"{origin.scheme}://{origin.netloc}",
				"profile": profile,
			},
		),
		content_type="text/html",
	)


def submit():
	from passport.oidc import issue_grant

	form = frappe.request.form
	if (
		any(len(form.getlist(k)) != 1 for k in form)
		or frappe.session.user in ("Guest", "Administrator")
		or not frappe.session.data.csrf_token
		or not hmac.compare_digest(form.get("csrf_token", ""), frappe.session.data.csrf_token)
	):
		return invalid_consent()
	if not store.get_valid_native_session(frappe.session.sid, frappe.session.user, lock=True):
		return invalid_consent()
	key = form.get("request_id", "")
	row = store.get("link_consent", key, lock=True)
	if (
		not row
		or row.get("spent")
		or row["exp"] <= time.time()
		or row["user"] != frappe.session.user
		or row["issuer_sid"] != frappe.session.sid
		or form.get("decision") not in ("allow", "cancel")
	):
		return invalid_consent()
	try:
		connections.check(row["user"], row["client"], row.get("connection_generation", 0))
	except ValueError:
		return invalid_consent()
	client = frappe.db.get_value(
		"Passport Client", {"name": row["client"], "enabled": 1}, ["name", "redirect_uri"], as_dict=True
	)
	if not client or client.redirect_uri != row["args"]["redirect_uri"]:
		return invalid_consent()
	row["spent"] = True
	store.put("link_consent", key, row)
	if form["decision"] == "cancel":
		frappe.db.commit()
		return redirect(
			client.redirect_uri
			+ "?"
			+ urlencode(
				{
					"state": row["args"]["state"],
					"error": "access_denied",
				}
			),
			code=303,
		)
	return issue_grant(client, row["args"], action="link")


def cleanup():
	for key, row in store.rows("link_consent"):
		if row["exp"] <= time.time():
			frappe.db.sql("DELETE FROM passport_state WHERE kind='link_consent' AND id=%s", key)


def invalid_consent():
	return Response(
		render_page(
			"passport",
			"Linking could not continue",
			{
				"page": "session",
				"title": "Linking could not continue",
				"message": "This request expired or your session changed. Start again from the site's linking page.",
				"link": "/account",
				"link_label": "Return to account",
			},
		),
		status=403,
		content_type="text/html",
	)
