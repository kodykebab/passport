"""Issuer account page. All mutations require CSRF plus a fresh password."""

import json
import time
from datetime import datetime, timezone

import frappe
from frappe.sessions import delete_session, get_csrf_token
from frappe.utils.password import check_password
from passport_core.ui import assets
from werkzeug.exceptions import Forbidden
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport import protocol, store


def insert(session):
	if session.user == "Guest":
		return
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
	if not store.native(sid, data.get("user")):
		delete_session(sid, user=data.get("user"), reason="Frappe ID session ended")
		return False


def confirm_password(user, password):
	"""Use native lockout policy, but never resolve a submitted account or OTP."""
	from frappe.auth import MAX_PASSWORD_SIZE, get_login_attempt_tracker

	trackers = [get_login_attempt_tracker(user), get_login_attempt_tracker(frappe.local.request_ip)]
	try:
		if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_SIZE:
			raise frappe.AuthenticationError("Incorrect User or Password")
		check_password(user, password, delete_tracker_cache=False)
	except frappe.AuthenticationError:
		for tracker in trackers:
			tracker.add_failure_attempt()
		raise
	for tracker in trackers:
		tracker.add_success_attempt()


def response():
	if frappe.request.path != "/account":
		return None
	if frappe.session.user == "Guest":
		return redirect("/login?redirect-to=/account")
	user = frappe.session.user
	if not store.native(frappe.session.sid, user):
		raise Forbidden("This browser session has ended")
	if frappe.request.method == "POST":
		# Require the explicit native token even when the framework accepts same-origin referrers.
		import hmac

		if not hmac.compare_digest(
			frappe.request.form.get("csrf_token", ""), frappe.session.data.csrf_token or ""
		):
			raise Forbidden("Invalid CSRF token")
		try:
			confirm_password(user, frappe.form_dict.get("password", ""))
		except frappe.AuthenticationError:
			return account_page(user, error="Incorrect password. Please try again.", status=403)
		except frappe.SecurityException:
			return account_page(
				user, error="Too many password attempts. Please wait before trying again.", status=429
			)
		action = frappe.form_dict.get("action")
		if action == "name":
			name = (frappe.form_dict.get("display_name") or "").strip()
			if not name or len(name) > 140:
				raise Forbidden("Enter a display name of at most 140 characters")
			doc = frappe.get_doc("User", user)
			doc.first_name, doc.middle_name, doc.last_name = name, "", ""
			doc.save(ignore_permissions=True)
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
		if not store.native(sid, user):
			continue
		data = json.loads(raw)
		sessions.append(
			{
				"device": protocol.digest(sid.encode()),
				"user_agent": data.get("user_agent") or "Unknown browser",
				"current": sid == frappe.session.sid,
			}
		)
	html = frappe.render_template(
		"templates/account.html",
		{
			"ui": assets("passport"),
			"csrf_token": get_csrf_token(),
			"profile": profile,
			"sessions": sessions,
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
