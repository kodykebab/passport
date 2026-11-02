import hmac
import secrets
import time
from html import escape
from urllib.parse import urlencode, urlsplit

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime
from passport_core.store import native_session_id
from werkzeug.wrappers import Response

from passport_client import admission, protocol, store
from passport_client.auth import PassportError


class _Acceptance:
	def __init__(self, invitation, identity):
		self.invitation = invitation
		self.identity = identity
		self.user = frappe.session.user
		self.access_epoch = identity.access_epoch
		self.identity_binding = (identity.issuer, identity.subject, identity.user)

	def permits(self, document):
		if frappe.session.user != self.user:
			return False
		if document is self.identity:
			previous = document.get_doc_before_save()
			return bool(
				previous
				and previous.access_epoch == self.access_epoch
				and (document.issuer, document.subject, document.user) == self.identity_binding
				and document.admitted == 1
			)
		return document is self.invitation and document.status == "Accepted"


def is_acceptance_write(document) -> bool:
	permission = getattr(document, "_passport_acceptance", None)
	return type(permission) is _Acceptance and permission.permits(document)


def _require_form():
	if frappe.request.method != "POST":
		raise frappe.PermissionError("Invitation changes require POST.")
	expected = frappe.session.data.get("csrf_token")
	supplied = frappe.request.headers.get("X-Frappe-CSRF-Token") or frappe.request.form.get("csrf_token")
	if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
		raise frappe.CSRFTokenError("Invalid invitation form. Reload the page and try again.")


@frappe.whitelist(methods=["POST"])
def begin(name: str, secret: str, password: str):
	_require_form()
	try:
		response = start(name, secret, password)
	except PassportError as error:
		raise frappe.PermissionError(str(error)) from error
	response.headers["Cache-Control"] = "no-store"
	response.headers["Referrer-Policy"] = "no-referrer"
	return response


@frappe.whitelist(methods=["POST"])
def complete(attempt_id: str, secret: str):
	from passport_client.linking import require_local_user

	_require_form()
	try:
		require_local_user()
	except PassportError as error:
		raise frappe.PermissionError(str(error)) from error
	accept(attempt_id, secret, frappe.request.cookies.get("passport_browser", ""))
	return Response(
		'{"accepted":true}',
		content_type="application/json",
		headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
	)


@frappe.whitelist(methods=["POST"])
def send_invitation(user: str) -> dict:
	from passport_client.auth import config

	_require_form()
	frappe.only_for("System Manager")
	policy = store.get(admission.POLICY_KIND, admission.POLICY_ID)
	if (
		not config()
		or frappe.conf.get("passport_disabled")
		or not frappe.conf.get("passport_allow_linking")
		or frappe.get_system_settings("enable_two_factor_auth")
		or frappe.get_system_settings("disable_user_pass_login")
		or not policy
		or policy.get("mode") != "enforced"
	):
		raise frappe.ValidationError("Enable Frappe ID linking and enforced admission before inviting users.")
	return {"name": issue_and_queue(user)}


@frappe.whitelist(methods=["POST"])
def cancel_invitation(name: str) -> dict:
	_require_form()
	cancel(name)
	return {"name": name}


def issue(user: str) -> tuple[str, str]:
	"""Return an invitation ID and delivery secret; the caller owns the transaction."""
	frappe.only_for("System Manager")
	if not isinstance(user, str) or not user:
		raise frappe.ValidationError("Select an existing local account.")
	secret = secrets.token_urlsafe(32)
	invitation = frappe.get_doc(
		{
			"doctype": "Passport Invitation",
			"user": user,
			"recipient_email": frappe.db.get_value("User", user, "email"),
			"inviter": frappe.session.user,
			"token_digest": protocol.digest(secret.encode()),
			"expires_at": add_to_date(now_datetime(), days=7),
			"status": "Pending",
		}
	).insert()
	return invitation.name, secret


def cancel(name: str) -> None:
	"""Cancel a pending invitation without changing the linked account's admission."""
	frappe.only_for("System Manager")
	invitation = frappe.get_doc("Passport Invitation", name, for_update=True)
	if invitation.status == "Cancelled":
		return
	invitation.status = "Cancelled"
	invitation.save(ignore_version=False)


def issue_and_queue(user: str) -> str:
	"""Create an invitation and native email queue entry in the caller's transaction."""
	frappe.only_for("System Manager")
	if not isinstance(user, str) or not user:
		raise frappe.ValidationError("Select an existing local account.")
	origin = frappe.conf.get("host_name", "").rstrip("/")
	address = urlsplit(origin)
	if (
		address.scheme not in ("https", "http")
		or not address.hostname
		or address.username
		or address.password
		or address.path
		or address.query
		or address.fragment
	):
		raise frappe.ValidationError("Configure this site's public URL before sending invitations.")
	frappe.db.savepoint("passport_invitation_delivery")
	try:
		recipient = frappe.get_doc("User", user, for_update=True)
		if not recipient.enabled or user in ("Guest", "Administrator"):
			raise frappe.ValidationError("Invite an existing enabled local account.")
		pending = frappe.db.get_value(
			"Passport Invitation",
			{
				"user": user,
				"recipient_email": recipient.email,
				"status": "Pending",
				"expires_at": (">", now_datetime()),
			},
			"name",
			for_update=True,
		)
		if pending:
			return pending
		name, secret = issue(user)
		link = origin + "/passport/invitation?" + urlencode({"name": name, "secret": secret})
		queued = frappe.sendmail(
			recipients=[recipient.email],
			subject=frappe._("Connect your Frappe ID"),
			message=(
				f"<p>You have been invited to connect your Frappe ID to {escape(origin)}.</p>"
				f'<p><a href="{escape(link, quote=True)}">Review invitation</a></p>'
				"<p>Sign in with your existing site account to review and accept. "
				"This invitation expires in seven days.</p>"
			),
			reference_doctype="Passport Invitation",
			reference_name=name,
			delayed=True,
			now=False,
		)
		if not queued:
			raise frappe.ValidationError("Email could not be queued. Check the site's email configuration.")
		return name
	except Exception:
		frappe.db.rollback(save_point="passport_invitation_delivery")
		raise


def start(name: str, secret: str, password: str):
	"""Prepare issuer consent after fresh native proof; called only by trusted site code."""
	from passport_core.password import confirm_password

	from passport_client.auth import config, start_authorization
	from passport_client.linking import require_local_user

	user = require_local_user()
	invitation = frappe.get_doc("Passport Invitation", name)
	_check_pending(invitation, user, protocol.digest(secret.encode()))
	confirm_password(user, password)
	require_local_user(lock=True)
	association = frappe.db.sql(
		"SELECT name,access_epoch FROM `tabPassport Identity` WHERE issuer=%s AND user=%s FOR UPDATE",
		(config().issuer, user),
	)
	if len(association) > 1:
		raise frappe.PermissionError("This account has conflicting identity links.")
	invitation = frappe.get_doc("Passport Invitation", name, for_update=True)
	_check_pending(invitation, user, protocol.digest(secret.encode()))
	return start_authorization(
		{
			"action": "link",
			"user": user,
			"local_sid": native_session_id(frappe.session.sid),
			"generation": store.generation(user, lock=True),
			"invitation": name,
			"invitation_digest": invitation.token_digest,
			"invitation_association": list(association[0]) if association else None,
		}
	)


def _check_pending(invitation, user, digest):
	address = frappe.db.get_value("User", user, "email")
	if (
		invitation.status != "Pending"
		or invitation.user != user
		or not hmac.compare_digest(invitation.token_digest, digest)
		or get_datetime(invitation.expires_at) <= now_datetime()
		or invitation.recipient_email != address
	):
		raise frappe.PermissionError("This invitation is unavailable for this account.")


def prepare(attempt_id, attempt, claims, identity, *, existing):
	"""Retain validated callback evidence without accepting or granting admission."""
	if not attempt_id or "invitation_association" not in attempt:
		raise frappe.PermissionError("Invitation proof was not started correctly.")
	expected = [identity.name, identity.access_epoch] if existing else None
	if attempt["invitation_association"] != expected:
		raise frappe.PermissionError("The identity association changed. Start again.")
	invitation = frappe.get_doc("Passport Invitation", attempt["invitation"], for_update=True)
	_check_pending(invitation, identity.user, attempt["invitation_digest"])
	current = store.get("attempt", attempt_id, lock=True)
	if current != attempt or not attempt.get("spent") or attempt.get("verified"):
		raise frappe.PermissionError("Invitation proof is no longer current.")
	deadline = min(attempt["exp"], claims["exp"])
	if deadline <= time.time():
		raise frappe.PermissionError("Invitation proof expired. Start again.")
	store.put(
		"attempt",
		attempt_id,
		{
			**attempt,
			"profile": {"email": claims.get("email", ""), "name": claims.get("name", "")},
			"verified": {
				"association_id": identity.name,
				"access_epoch": identity.access_epoch,
				"issuer": identity.issuer,
				"subject": identity.subject,
				"exp": deadline,
			},
		},
	)


def accept(attempt_id: str, secret: str, browser: str) -> str:
	"""Accept verified proof atomically in the caller's transaction."""
	if not all(isinstance(value, str) and value for value in (attempt_id, secret, browser)):
		raise frappe.PermissionError("Invalid invitation proof.")
	checkpoint = "invitation_" + secrets.token_hex(8)
	frappe.db.savepoint(checkpoint)
	try:
		return _accept(attempt_id, secret, browser)
	except Exception:
		frappe.db.rollback(save_point=checkpoint)
		raise


def _accept(attempt_id, secret, browser):
	attempt = store.get("attempt", attempt_id) or {}
	name = attempt.get("invitation")
	if not name or not attempt.get("verified"):
		raise frappe.PermissionError("Invitation proof is unavailable.")
	initial = frappe.get_doc("Passport Invitation", name)
	if initial.user != frappe.session.user:
		raise frappe.PermissionError("Sign in to the invited local account.")
	users = sorted({initial.user, initial.inviter})
	for user in users:
		frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", user)
	_check_inviter(initial.inviter)
	native = store.get_valid_native_session(frappe.session.sid, frappe.session.user, lock=True)
	if not native or native.get("passport") or store.get("federation", native_session_id(frappe.session.sid)):
		raise frappe.PermissionError("Use the invited account's local session.")
	proof = attempt["verified"]
	identity = frappe.get_doc("Passport Identity", proof["association_id"], for_update=True)
	policy = store.get(admission.POLICY_KIND, admission.POLICY_ID, lock=True)
	invitation = frappe.get_doc("Passport Invitation", name, for_update=True)
	current = store.get("attempt", attempt_id, lock=True)
	if current != attempt or (invitation.user, invitation.inviter) != (initial.user, initial.inviter):
		raise frappe.PermissionError("Invitation proof changed. Start again.")
	_check_proof(invitation, attempt, secret, browser)
	if invitation.status == "Accepted":
		if invitation.accepted_attempt != attempt_id:
			raise frappe.PermissionError("This invitation was already accepted.")
		return invitation.association
	if invitation.status != "Pending" or not policy or policy.get("mode") != "enforced":
		raise frappe.PermissionError("Invitation admission is unavailable.")
	if (identity.issuer, identity.subject, identity.user, identity.access_epoch) != (
		proof["issuer"],
		proof["subject"],
		invitation.user,
		proof["access_epoch"],
	):
		raise frappe.PermissionError("The identity binding changed. Start again.")
	_grant(invitation, identity, attempt_id)
	return identity.name


def _check_inviter(user):
	if not frappe.db.sql("SELECT enabled FROM tabUser WHERE name=%s AND enabled=1 FOR UPDATE", user):
		raise frappe.PermissionError("The inviter is no longer authorized.")
	if user != "Administrator" and not frappe.db.sql(
		"""SELECT name FROM `tabHas Role` WHERE parent=%s AND parenttype='User'
		AND parentfield='roles' AND role='System Manager' FOR UPDATE""",
		user,
	):
		raise frappe.PermissionError("The inviter is no longer authorized.")


def _check_proof(invitation, attempt, secret, browser):
	valid = (
		attempt.get("action") == "link"
		and attempt.get("spent")
		and attempt.get("user") == invitation.user == frappe.session.user
		and attempt.get("local_sid") == native_session_id(frappe.session.sid)
		and attempt.get("generation") == store.generation(invitation.user, lock=True)
		and attempt.get("exp", 0) > time.time()
		and attempt["verified"].get("exp", 0) > time.time()
		and hmac.compare_digest(attempt.get("browser", ""), protocol.digest(browser.encode()))
		and hmac.compare_digest(invitation.token_digest, protocol.digest(secret.encode()))
		and attempt.get("invitation_digest") == invitation.token_digest
		and get_datetime(invitation.expires_at) > now_datetime()
	)
	address = frappe.db.sql("SELECT email FROM tabUser WHERE name=%s FOR UPDATE", invitation.user)
	if not valid or not address or address[0][0] != invitation.recipient_email:
		raise frappe.PermissionError("Invitation proof expired or no longer matches this account.")


def _grant(invitation, identity, attempt_id):
	permission = _Acceptance(invitation, identity)
	identity._passport_acceptance = permission
	invitation._passport_acceptance = permission
	try:
		identity.admitted = 1
		identity.save(ignore_permissions=True, ignore_version=False)
		invitation.status = "Accepted"
		invitation.association = identity.name
		invitation.accepted_epoch = identity.access_epoch
		invitation.accepted_attempt = attempt_id
		invitation.accepted_by = frappe.session.user
		invitation.accepted_at = now_datetime()
		invitation.save(ignore_permissions=True, ignore_version=False)
		from passport_client import discovery

		discovery.record(identity.issuer, identity.subject, True)
	finally:
		del identity._passport_acceptance
		del invitation._passport_acceptance
