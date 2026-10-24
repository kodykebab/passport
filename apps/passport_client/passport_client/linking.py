"""Link an existing local account after a fresh password and issuer consent."""

import hmac
import time

import frappe
from frappe.sessions import get_csrf_token
from passport_core.password import confirm_password
from passport_core.ui import render_page
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport_client import protocol, store

PATH = "/passport/link"


def require_local_user(*, lock=False):
	from passport_client.auth import PassportError, config

	user = frappe.session.user
	if (
		not config()
		or not frappe.conf.get("passport_allow_linking")
		or frappe.conf.get("passport_disabled")
		or user in ("Guest", "Administrator")
		or frappe.get_system_settings("enable_two_factor_auth")
		or frappe.get_system_settings("disable_user_pass_login")
	):
		raise PassportError("Linking is unavailable. Sign in locally or contact the site administrator.")
	native = store.get_valid_native_session(frappe.session.sid, user, lock=lock)
	if not native or native.get("passport") or store.get("federation", frappe.session.sid):
		raise PassportError("Sign in with this site's local account before linking a Frappe ID.")
	return user


def response():
	from passport_client.auth import PassportError, config, start_authorization

	if frappe.session.user == "Guest":
		return redirect("/login?redirect-to=/passport/link")
	user = require_local_user()
	linked = frappe.db.exists("Passport Identity", {"issuer": config().issuer, "user": user})
	error, status = None, 200
	if frappe.request.method == "POST":
		if not frappe.session.data.csrf_token or not hmac.compare_digest(
			frappe.request.form.get("csrf_token", ""), frappe.session.data.csrf_token
		):
			raise PassportError("Invalid form. Reload this page and try again.")
		if linked:
			raise PassportError("This account already has a Frappe ID link. Existing links are not replaced.")
		try:
			confirm_password(user, frappe.request.form.get("password", ""))
		except frappe.AuthenticationError:
			error, status = "Incorrect password. Please try again.", 403
		except frappe.SecurityException:
			error, status = "Too many password attempts. Please wait before trying again.", 429
		else:
			require_local_user(lock=True)
			return start_authorization(
				{
					"action": "link",
					"user": user,
					"local_sid": frappe.session.sid,
					"generation": store.generation(user, lock=True),
				}
			)
	return Response(
		render_page(
			"passport_client",
			"Link your Frappe ID",
			{
				"page": "link",
				"title": "Link your Frappe ID",
				"user": user,
				"linked": bool(linked),
				"csrf_token": get_csrf_token(),
				"error": error,
			},
		),
		status=status,
		content_type="text/html",
	)


def finish(attempt, claims):
	from passport_client.auth import PassportError, config

	user = require_local_user(lock=True)
	if (
		attempt["exp"] <= time.time()
		or user != attempt["user"]
		or frappe.session.sid != attempt["local_sid"]
		or store.generation(user, lock=True) != attempt["generation"]
	):
		raise PassportError("The local session changed. Start linking again.")
	issuer = config().issuer
	# Serialize linking for this issuer before locking identity-index gaps.
	registration = protocol.digest(issuer.encode())
	store.put("link_registration", registration, {})
	if frappe.db.sql(
		"SELECT name FROM `tabPassport Identity` WHERE issuer=%s AND (user=%s OR subject=%s) FOR UPDATE",
		(issuer, user, claims["sub"]),
	):
		raise PassportError("An account is already linked. Existing links are not replaced.")
	try:
		frappe.get_doc(
			{"doctype": "Passport Identity", "issuer": issuer, "subject": claims["sub"], "user": user}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError, frappe.UniqueValidationError:
		raise PassportError("An account is already linked. Existing links are not replaced.")
	frappe.db.sql("DELETE FROM passport_state WHERE kind='link_registration' AND id=%s", registration)
	frappe.db.commit()
	return redirect(PATH, code=303)
