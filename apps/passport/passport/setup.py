import json

import frappe
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from passport import connections, oidc


def approve_enrollment(request: dict, title: str):
	frappe.only_for("System Manager")
	if not isinstance(request, dict) or set(request) != {"issuer", "client_id", "home_url", "public_jwk"}:
		raise frappe.ValidationError("Invalid enrollment request.")
	key = request["public_jwk"]
	if not isinstance(key, dict) or set(key) != {"kty", "crv", "alg", "use", "kid", "x"}:
		raise frappe.ValidationError("Only a public signing key may be enrolled.")
	if (
		request["issuer"] != oidc.issuer()
		or not isinstance(request["client_id"], str)
		or not 0 < len(request["client_id"]) <= 140
		or not isinstance(request["home_url"], str)
		or connections.site_origin(request["home_url"] + "/passport/callback") != request["home_url"]
		or key.get("kty") != "OKP"
		or key.get("crv") != "Ed25519"
		or key.get("alg") != "EdDSA"
		or key.get("use") != "sig"
		or not isinstance(key.get("kid"), str)
		or not key["kid"]
	):
		raise frappe.ValidationError("Enrollment origin, issuer or signing key is invalid.")
	try:
		valid_key = isinstance(jwt.PyJWK.from_dict(key).key, Ed25519PublicKey)
	except jwt.PyJWTError, ValueError, TypeError:
		valid_key = False
	if not valid_key:
		raise frappe.ValidationError("Invalid enrollment signing key.")
	return register_client(request["client_id"], title, json.dumps(key, sort_keys=True), request["home_url"])


def register_client(client_id: str, title: str, public_jwk: str, home_url: str):
	frappe.only_for("System Manager")
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
	client.save()
	frappe.db.commit()
	return {"client_id": client.name, "installation": client.registration_revision}


def provision_iteration():
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
