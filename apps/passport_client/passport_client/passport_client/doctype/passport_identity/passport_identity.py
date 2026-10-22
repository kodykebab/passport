import frappe
from frappe.model.document import Document


class PassportIdentity(Document):
	def validate(self):
		if self.user in ("Administrator", "Guest"):
			frappe.throw(f"{self.user} cannot be linked to Frappe ID")


def on_doctype_update():
	frappe.db.add_unique("Passport Identity", ["issuer", "subject"], constraint_name="unique_issuer_subject")
