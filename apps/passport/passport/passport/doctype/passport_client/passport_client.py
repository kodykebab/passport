import secrets

from frappe.model.document import Document


class PassportClient(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if not previous or any(
			self.get(field) != previous.get(field)
			for field in ("public_jwk", "home_url", "redirect_uri", "enabled")
		):
			self.registration_revision = secrets.token_hex(16)
		else:
			self.registration_revision = previous.registration_revision
