"""Local SQL authority and lease application. Network work holds no SQL locks."""

import json
import secrets
import time

import frappe
from werkzeug.exceptions import Forbidden

from passport_client import store

BACKGROUND = {
	"/passport/status",
	"/passport/retry",
	"/api/method/frappe.realtime.get_user_info",
	"/api/method/frappe.realtime.has_permission",
}


def boundary():
	path = frappe.request.path
	if path.startswith("/passport/") or frappe.request.cookies.get("sid"):
		if "sid" in frappe.form_dict or "cmd" in frappe.form_dict:
			raise Forbidden("Invalid session parameters")
	if path in BACKGROUND:
		frappe.flags.passport_background = True


def insert(session):
	if pending := frappe.flags.get("passport_pending"):
		session.data.data.passport_generation = store.generation(session.user, lock=True)
		session.data.data.passport = True
		store.create("federation", session.sid, pending)


def deleted(sid, user=None):
	row = store.get("federation", sid, lock=True)
	if row:
		row["revoked"] = True
		row["revision"] += 1
		store.put("federation", sid, row)


def local_valid(sid, row, lock=False):
	if not row or row["revoked"] or row["deadline"] <= time.time():
		return None
	native = store.native(sid, row["user"], lock=lock)
	if not native or not native.get("passport"):
		return None
	if frappe.conf.get("passport_disabled") or not frappe.db.exists(
		"Passport Identity", {"issuer": row["issuer"], "subject": row["sub"], "user": row["user"]}
	):
		return None
	return native


def validate(sid, data):
	row = store.get("federation", sid)
	if not row and not (data and data.get("passport")):
		# A stale cache entry cannot erase the durable authentication-method marker.
		raw = frappe.db.sql("SELECT sessiondata FROM tabSessions WHERE sid=%s", sid)
		if not raw or not json.loads(raw[0][0]).get("passport"):
			return
	if frappe.request.path == "/api/method/logout":
		return
	if not local_valid(sid, row):
		# Terminal native/identity state must reach Frappe's cookie clearing and
		# Guest fallback. Only an expired lease retains a resumable session.
		if data:
			from frappe.sessions import delete_session

			delete_session(sid, user=row["user"] if row else data.get("user"))
		return False
	path = frappe.request.path
	if path in ("/passport/retry", "/passport/status"):
		return
	if row["exp"] <= time.time():
		from passport_client.auth import session_page

		raise Forbidden(
			response=session_page(
				"Session validation required",
				"Your lease has expired. Interrupted writes will not be replayed.",
				"/passport/status",
				"Validate session",
				status=403,
			)
		)
	# Socket requests must not extend native idle time.
	if path in BACKGROUND:
		frappe.flags.passport_background = True


def renew(complete=False):
	from passport_client.auth import PassportError, call, verify

	sid = frappe.session.sid
	row = store.get("federation", sid)
	if not local_valid(sid, row):
		raise PassportError("This local session is no longer valid.")
	revision = row["revision"]
	operation = secrets.token_hex(24)
	body = {
		"sid": row["protocol_sid"],
		"binding": row["binding"],
		"operation": operation,
		"deadline": row["deadline"],
	}
	frappe.db.commit()
	response = call("/passport/complete" if complete else "/passport/renew", body)
	claims = verify(response["lease"], "passport-lease+jwt", ["sub", "sid", "binding", "operation"])
	if (
		any(claims[k] != body[k] for k in ("sid", "binding", "operation"))
		or claims["sub"] != row["sub"]
		or claims["exp"] - claims["iat"] > 300
	):
		raise PassportError("Invalid lease.")
	# Lock User then native then federation, matching disablement/deletion ordering.
	if not local_valid(sid, row, lock=True):
		raise PassportError("This local session ended during validation.")
	current = store.get("federation", sid, lock=True)
	if (
		not current
		or current["revoked"]
		or current["revision"] != revision
		or current["binding"] != row["binding"]
	):
		raise PassportError("This local session changed during validation.")
	current["exp"] = min(claims["exp"], row["deadline"])
	current["revision"] += 1
	store.put("federation", sid, current)
	frappe.db.commit()
	return current["exp"]


def socket_context():
	row = store.get("federation", frappe.session.sid)
	if not row:
		return None
	native = local_valid(frappe.session.sid, row)
	if not native:
		raise Forbidden()
	return max(0, min(row["exp"] - time.time(), row["deadline"] - time.time(), native["idle_remaining"]))


def user_changed(doc, method=None):
	if doc.enabled:
		return
	store.revoke_user(doc.name)
