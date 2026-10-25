"""Verified issuer-only registration; never provision client access."""

import frappe
from frappe.auth import MAX_PASSWORD_SIZE
from frappe.core.doctype.user.user import test_password_strength

from passport import deletion, protocol, store

PATH = "/passport/signup"


def enabled():
	return bool(
		frappe.conf.get("passport_allow_signup")
		and frappe.conf.get("passport_email_code_key")
		and frappe.get_website_settings("disable_signup")
		and not frappe.get_system_settings("enable_two_factor_auth")
	)


def available_email(email):
	return (
		enabled()
		and not frappe.db.exists("User", {"email": email})
		and not frappe.db.exists("User", email)
		and not deletion.pending(email)
	)


def validate_profile(email, display_name, password):
	from passport.email_login import ChallengeError

	display_name = display_name.strip()
	if not display_name or len(display_name) > 140:
		raise ChallengeError("Enter a display name of at most 140 characters.")
	if not password or len(password) > MAX_PASSWORD_SIZE:
		raise ChallengeError(f"Enter a password of at most {MAX_PASSWORD_SIZE} characters.")
	result = test_password_strength(password, user_data=(display_name, email))
	feedback = result.get("feedback")
	if feedback and not feedback.get("password_policy_validation_passed"):
		raise ChallengeError("Choose a stronger password that meets this site's password policy.")
	return display_name


def create_verified_user(email, display_name, password):
	from passport.email_login import ChallengeError

	# Serialize native User creation for this address before its insert hooks run.
	registration = protocol.digest(email.encode())
	store.put("signup_registration", registration, {})
	if not available_email(email):
		raise ChallengeError("Signup is unavailable for this address. Try signing in.")
	limit = int(frappe.get_system_settings("max_signups_allowed_per_hour") or 300)
	if frappe.db.get_creation_count("User", 60) >= limit:
		raise ChallengeError("Signup is temporarily unavailable. Please try again later.")
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": display_name,
			"enabled": 1,
			"user_type": "Website User",
			"new_password": password,
			"send_welcome_email": 0,
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	if user.user_type != "Website User" or user.roles:
		raise ChallengeError("Signup could not create an issuer-only account. Contact the administrator.")
	frappe.get_doc({"doctype": "Passport Account", "user": user.name}).insert(ignore_permissions=True)
	generation = store.generation(user.name, lock=True)
	frappe.db.sql("DELETE FROM passport_state WHERE kind='signup_registration' AND id=%s", registration)
	frappe.db.commit()
	return user.name, generation
