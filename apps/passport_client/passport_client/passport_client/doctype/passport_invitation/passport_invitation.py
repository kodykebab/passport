import re

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class PassportInvitation(Document):
	def validate(self):
		from passport_client.invitations import is_acceptance_write

		acceptance = is_acceptance_write(self)
		if not acceptance:
			frappe.only_for("System Manager")
		previous = self.get_doc_before_save()
		outcome = ("association", "accepted_epoch", "accepted_attempt", "accepted_by", "accepted_at")
		if not acceptance and any(
			self.get(field) != (previous.get(field) if previous else None) for field in outcome
		):
			raise frappe.ValidationError("Invitation outcomes cannot be edited.")
		if previous:
			immutable = ("user", "recipient_email", "inviter", "token_digest", "expires_at")
			if any(self.get(field) != previous.get(field) for field in immutable):
				raise frappe.ValidationError(
					"Cancel this invitation and issue a new one to change its terms."
				)
			transition = ("Pending", "Accepted") if acceptance else ("Pending", "Cancelled")
			if self.status != previous.status and (previous.status, self.status) != transition:
				raise frappe.ValidationError("This invitation transition is not allowed.")
			return
		if self.status != "Pending" or self.inviter != frappe.session.user:
			raise frappe.ValidationError("New invitations must be pending and issued by the current user.")
		if not re.fullmatch(r"[0-9a-f]{64}", self.token_digest or ""):
			raise frappe.ValidationError("Invalid invitation secret digest.")
		if not self.expires_at or get_datetime(self.expires_at) <= now_datetime():
			raise frappe.ValidationError("Invitation expiry must be in the future.")
		user = frappe.db.get_value("User", self.user, ["enabled", "email"], as_dict=True)
		if self.user in ("Guest", "Administrator") or not user or not user.enabled:
			raise frappe.ValidationError("Invite an existing enabled local account.")
		if not user.email or self.recipient_email != user.email:
			raise frappe.ValidationError("Invitation delivery must use the current local account address.")
