"""Issuer account page. All mutations require CSRF plus a fresh password."""

import json
import time
from datetime import datetime, timezone

import frappe
from frappe.sessions import delete_session, get_csrf_token
from passport_core.password import confirm_password
from passport_core.ui import render_page
from werkzeug.exceptions import Forbidden
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport import connections, deletion, protocol, store


def insert(session):
	if session.user == "Guest":
		return
	if deletion.pending(session.user):
		raise frappe.AuthenticationError("Account deletion is pending")
	end = datetime.fromtimestamp(time.time() + 7 * 86400, timezone.utc)
	if session.data.data.get("session_end"):
		end = min(end, datetime.fromisoformat(session.data.data.session_end))
	session.data.data.session_end = end.isoformat()
	generation = store.generation(session.user, lock=True)
	context = frappe.flags.get("passport_email_auth")
	if context and (
		context["user"] != session.user
		or context["generation"] != generation
		or frappe.get_system_settings("enable_two_factor_auth")
	):
		raise frappe.AuthenticationError("Email login is no longer valid")
	session.data.data.passport_auth_method = "email_code" if context else "password"
	session.data.data.passport_auth_time = context["auth_time"] if context else int(time.time())
	session.data.data.passport_generation = generation


def validate_session(sid, data):
	if not data:
		return False
	if not store.get_valid_native_session(sid, data.get("user")):
		delete_session(sid, user=data.get("user"), reason="Frappe ID session ended")
		return False


def response():
	if frappe.request.path != "/account":
		return None
	if frappe.session.user == "Guest":
		return redirect("/login?redirect-to=/account")
	user = frappe.session.user
	if not store.get_valid_native_session(frappe.session.sid, user):
		raise Forbidden("This browser session has ended")
	if frappe.request.method == "POST":
		# Require the explicit native token even when the framework accepts same-origin referrers.
		import hmac

		if not hmac.compare_digest(
			frappe.request.form.get("csrf_token", ""), frappe.session.data.csrf_token or ""
		):
			raise Forbidden("Invalid CSRF token")
		if not store.get_valid_native_session(frappe.session.sid, user, lock=True):
			raise Forbidden("This browser session has ended")
		try:
			confirm_password(user, frappe.form_dict.get("password", ""))
		except frappe.AuthenticationError:
			return account_page(user, error="Incorrect password. Please try again.", status=403)
		except frappe.SecurityException:
			return account_page(
				user, error="Too many password attempts. Please wait before trying again.", status=429
			)
		action = frappe.form_dict.get("action")
		if action == "delete":
			if frappe.form_dict.get("confirmation") != "DELETE":
				return account_page(user, error="Type DELETE to confirm account deletion.", status=400)
			try:
				complete = deletion.request(user)
			except ValueError as exc:
				return account_page(user, error=str(exc), status=403)
			from frappe.auth import clear_cookies

			clear_cookies()
			return Response(
				render_page(
					"passport",
					"Account deleted" if complete else "Deletion pending",
					{
						"page": "session",
						"title": "Account deleted" if complete else "Deletion pending",
						"message": "Your Frappe ID login and profile have been deleted. Site accounts and data remain unchanged."
						if complete
						else "Your Frappe ID is disabled. Deletion could not finish and will be retried. Contact the issuer administrator if it remains pending.",
						"link": "/login",
						"link_label": "Return to sign in",
					},
				),
				content_type="text/html",
				headers={"Cache-Control": "no-store"},
			)
		if action == "name":
			name = (frappe.form_dict.get("display_name") or "").strip()
			if not name or len(name) > 140:
				raise Forbidden("Enter a display name of at most 140 characters")
			doc = frappe.get_doc("User", user)
			doc.first_name, doc.middle_name, doc.last_name = name, "", ""
			doc.save(ignore_permissions=True)
		elif action in ("revoke_site", "allow_site"):
			try:
				connections.set_revoked(user, frappe.form_dict.get("client", ""), action == "revoke_site")
			except ValueError:
				raise Forbidden("Unknown connected site")
		elif action in ("device", "everywhere"):
			# Account lock serializes disablement and revocation with new lease issuance.
			frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", user)
			rows = frappe.db.sql("SELECT sid FROM tabSessions WHERE user=%s FOR UPDATE", user)
			target = frappe.form_dict.get("device")
			for (sid,) in rows:
				if action == "everywhere" or protocol.digest(sid.encode()) == target:
					delete_session(sid, user=user, reason="Frappe ID account logout")
		else:
			raise Forbidden("Invalid action")
		frappe.db.commit()
		return redirect("/account")
	if frappe.request.method != "GET":
		return Response(status=405)
	return account_page(user)


def account_page(user, *, error=None, status=200):
	profile = frappe.db.get_value("User", user, ["email", "full_name"], as_dict=True)
	sessions = []
	for sid, raw in frappe.db.sql("SELECT sid,sessiondata FROM tabSessions WHERE user=%s", user):
		if not store.get_valid_native_session(sid, user):
			continue
		data = json.loads(raw)
		sessions.append(
			{
				"device": protocol.digest(sid.encode()),
				"user_agent": data.get("user_agent") or "Unknown browser",
				"current": sid == frappe.session.sid,
			}
		)
	html = render_page(
		"passport",
		"Frappe ID account",
		{
			"page": "account",
			"csrf_token": get_csrf_token(),
			"profile": profile,
			"sessions": sessions,
			"connections": connections.sites(user),
			"can_delete": deletion.eligible(user),
			"error": error,
		},
	)
	return Response(
		html,
		status=status,
		content_type="text/html",
		headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
	)


def user_changed(doc, method=None):
	if doc.enabled:
		return
	store.revoke_user(doc.name)
