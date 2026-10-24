"""Construct protocol submissions from the email page's server-provided data."""

from page_data import page_data


class Forms:
	def __init__(self, html):
		self.data = page_data(html)

	def action(self, action):
		data = self.data
		fields = {"action": action, "attempt": data["attempt"], "form_token": data["form_token"]}
		if action == "send":
			fields["redirect-to"] = data["destination"]
		elif action == "verify":
			fields["generation"] = data["challenge"]["generation"]
		return fields
