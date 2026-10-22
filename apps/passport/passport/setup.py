import frappe
from frappe.utils.password import update_password


def create_demo_user(email: str, password: str):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0].title(),
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		)
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)
	update_password(email, password)
	frappe.db.commit()


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
