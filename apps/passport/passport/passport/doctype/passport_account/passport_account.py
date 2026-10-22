import uuid

import frappe
from frappe.model.document import Document


class PassportAccount(Document):
	def autoname(self):
		# The subject never changes, even if the user's email does.
		self.subject = str(uuid.uuid4())
		self.name = self.subject

	def validate(self):
		if self.user in ("Administrator", "Guest"):
			frappe.throw(f"{self.user} cannot have a Frappe ID account")
