import hmac
import json
from urllib.parse import urlencode

import frappe
from frappe.sessions import get_csrf_token
from passport_core.ui import render_page
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport_client import invitations, protocol, store
from passport_client.auth import PassportError, config
from passport_client.linking import require_local_user

PATH = "/passport/invitation"
COOKIE = "passport_invitation"


def response():
	try:
		result = _response()
	except frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError, PassportError:
		frappe.db.rollback()
		result = page(
			"unavailable",
			error="This invitation is unavailable. Use the invited site's local account or ask for a new invitation.",
			status=403,
		)
	result.headers["Cache-Control"] = "no-store"
	result.headers["Referrer-Policy"] = "no-referrer"
	return result


def _response():
	if frappe.request.method == "GET" and frappe.request.args.get("secret"):
		name, secret = frappe.request.args.get("name", ""), frappe.request.args["secret"]
		if len(name) > 140 or len(secret) > 128 or not name:
			raise frappe.PermissionError("Invalid invitation.")
		result = redirect(PATH, code=303)
		result.set_cookie(
			COOKIE,
			json.dumps({"name": name, "secret": secret}),
			max_age=600,
			httponly=True,
			secure=frappe.request.scheme == "https",
			samesite="Lax",
			path=PATH,
		)
		return result
	if frappe.session.user == "Guest":
		return redirect("/login?" + urlencode({"redirect-to": PATH}), code=303)
	user = require_local_user()
	try:
		context = json.loads(frappe.request.cookies.get(COOKIE, "{}"))
		name, secret = context["name"], context["secret"]
		if not isinstance(name, str) or not isinstance(secret, str):
			raise ValueError
	except ValueError, KeyError, TypeError:
		raise frappe.PermissionError("Open your invitation link again.")
	invitation = frappe.get_doc("Passport Invitation", name)
	if invitation.user != user or not hmac.compare_digest(
		invitation.token_digest, protocol.digest(secret.encode())
	):
		raise frappe.PermissionError("Use the invited local account.")
	attempt_id = frappe.request.values.get("attempt", "")
	if frappe.request.method == "POST":
		invitations._require_form()
		if frappe.request.form.get("action") == "begin":
			try:
				return invitations.start(name, secret, frappe.request.form.get("password", ""))
			except frappe.SecurityException:
				return page(
					"confirm",
					error="Too many password attempts. Please wait before trying again.",
					status=429,
				)
			except frappe.AuthenticationError:
				return page(
					"confirm", error="Could not confirm your password. Check it and try again.", status=403
				)
		if frappe.request.form.get("action") != "accept":
			raise frappe.PermissionError("Invalid invitation action.")
		invitations.accept(attempt_id, secret, frappe.request.cookies.get("passport_browser", ""))
		frappe.db.commit()
		result = page("success")
		result.delete_cookie(COOKIE, path=PATH)
		return result
	if attempt_id:
		attempt = store.get("attempt", attempt_id)
		if not attempt or attempt.get("invitation") != name or not attempt.get("verified"):
			raise frappe.PermissionError("Invitation proof is unavailable.")
		invitations._check_proof(
			invitation, attempt, secret, frappe.request.cookies.get("passport_browser", "")
		)
		invitations._check_pending(invitation, user, invitation.token_digest)
		return page("accept", attempt=attempt_id, profile=attempt.get("profile", {}))
	invitations._check_pending(invitation, user, invitation.token_digest)
	return page("confirm")


def page(stage, *, error=None, status=200, attempt="", profile=None):
	titles = {
		"confirm": "Connect your Frappe ID",
		"accept": "Accept your invitation",
		"success": "Invitation accepted",
		"unavailable": "Invitation unavailable",
	}
	return Response(
		render_page(
			"passport_client",
			titles[stage],
			{
				"page": "invitation",
				"title": titles[stage],
				"stage": stage,
				"error": error,
				"user": frappe.session.user,
				"site": frappe.conf.host_name,
				"csrf_token": get_csrf_token(),
				"attempt": attempt,
				"profile": profile or {},
				"passport_url": config().issuer + "/sites" if config() else None,
			},
		),
		status=status,
		content_type="text/html",
	)
