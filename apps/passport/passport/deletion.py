"""Durable self-service deletion, fenced before native hooks can commit."""

import frappe
from frappe.sessions import delete_session

from passport import store


def eligible(user):
	return bool(
		user not in ("Guest", "Administrator")
		and "System Manager" not in frappe.get_roles(user)
		and frappe.db.exists("Passport Account", {"user": user})
	)


def pending(user):
	return bool(store.rows("account_deletion", user=user))


def request(user):
	if not eligible(user):
		raise ValueError("This account requires administrator-managed deletion.")
	subject = frappe.db.get_value("Passport Account", {"user": user})
	store.put(
		"account_deletion",
		subject,
		{"user": user, "created": str(frappe.db.get_value("User", user, "creation"))},
	)
	frappe.db.set_value("User", user, "enabled", 0)
	store.revoke_user(user)
	frappe.db.commit()
	return finish(subject)


def finish(subject):
	intent = store.get("account_deletion", subject)
	if not intent or intent.get("deleted"):
		return True
	user = intent["user"]
	try:
		frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", user)
		intent = store.get("account_deletion", subject, lock=True)
		if not intent or intent.get("deleted"):
			return True
		if frappe.db.exists("User", user):
			if str(frappe.db.get_value("User", user, "creation")) != intent["created"]:
				raise ValueError("The original user has been replaced")
			frappe.db.set_value("User", user, "enabled", 0)
		for (sid,) in frappe.db.sql("SELECT sid FROM tabSessions WHERE user=%s", user):
			delete_session(sid, user=user, reason="Frappe ID account deletion")
		if frappe.db.exists("Passport Account", subject):
			frappe.delete_doc("Passport Account", subject, ignore_permissions=True, delete_permanently=True)
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, ignore_permissions=True, delete_permanently=True)
		frappe.db.sql("DELETE FROM passport_state WHERE user=%s AND kind!='account_deletion'", user)
		store.put("account_deletion", subject, {"deleted": True})
		frappe.db.commit()
		return True
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title="Frappe ID deletion pending",
			message=f"Native deletion failed for subject {subject}. Review linked records and retry passport.deletion.finish.",
		)
		return False


def retry_pending():
	for subject, row in store.rows("account_deletion"):
		if not row.get("deleted"):
			finish(subject)
