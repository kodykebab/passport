import secrets

import frappe
from frappe.model.document import Document


class PassportIdentity(Document):
	def on_update(self):
		from passport_client import discovery, store

		previous = self.get_doc_before_save()
		if previous and (previous.issuer, previous.subject, previous.user) != (
			self.issuer,
			self.subject,
			self.user,
		):
			discovery.record(previous.issuer, previous.subject, False)
		policy = store.get("admission_policy", "site") or {}
		if not self.admitted and policy.get("mode") == "enforced":
			discovery.record(self.issuer, self.subject, False)

	def on_trash(self):
		from passport_client import discovery

		discovery.record(self.issuer, self.subject, False)

	def validate(self):
		if self.user in ("Administrator", "Guest"):
			frappe.throw(f"{self.user} cannot be linked to Frappe ID")
		previous = self.get_doc_before_save()
		binding_changed = previous and any(
			self.get(field) != previous.get(field) for field in ("issuer", "subject", "user")
		)
		if binding_changed:
			self.admitted = 0
		admission_changed = self.admitted != (previous.admitted if previous else 0)
		if admission_changed:
			from passport_client.invitations import is_acceptance_write

			if not is_acceptance_write(self):
				frappe.only_for("System Manager")
		if not previous or binding_changed or admission_changed:
			self.access_epoch = secrets.token_hex(16)
		else:
			self.access_epoch = previous.access_epoch


def on_doctype_update():
	frappe.db.add_unique("Passport Identity", ["issuer", "subject"], constraint_name="unique_issuer_subject")
