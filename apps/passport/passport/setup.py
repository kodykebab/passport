import frappe


def register_client(client_id: str, title: str, public_jwk: str, home_url: str):
	client = (
		frappe.get_doc("Passport Client", client_id)
		if frappe.db.exists("Passport Client", client_id)
		else frappe.new_doc("Passport Client")
	)
	client.client_id = client_id
	client.update(
		{
			"title": title,
			"public_jwk": public_jwk,
			"home_url": home_url,
			"redirect_uri": home_url + "/passport/callback",
			"enabled": 1,
		}
	)
	client.save(ignore_permissions=True)
	frappe.db.commit()


def provision_iteration():
	import json
	from pathlib import Path

	from passport.protocol import public_jwk

	config = json.loads(Path("site1.localhost/site_config.json").read_text())
	register_client(
		"site1.localhost",
		"site1",
		json.dumps(public_jwk(config["passport_private_key"], config["passport_key_id"])),
		config["host_name"],
	)


def provision_email_login():
	"""Enable email challenges without changing the site's mail provider or sender."""
	import secrets

	from frappe.installer import update_site_config

	if not frappe.conf.get("passport_email_code_key"):
		update_site_config("passport_email_code_key", secrets.token_hex(32))
