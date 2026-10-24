"""Fresh password confirmation without changing the authenticated session."""

import frappe
from frappe.utils.password import check_password


def confirm_password(user, password):
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
