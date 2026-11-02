import json
import re
import secrets

import frappe
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from passport_client import admission, auth, protocol


def generate_key():
	key = (
		Ed25519PrivateKey.generate()
		.private_bytes(
			serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
		)
		.decode()
	)
	kid = secrets.token_hex(16)
	return {"private_key": key, "key_id": kid, "public_jwk": protocol.public_jwk(key, kid)}


def review():
	frappe.only_for("System Manager")
	identities = frappe.get_all(
		"Passport Identity",
		fields=["name", "user", "issuer", "subject", "admitted", "access_epoch"],
		order_by="name",
	)
	return {
		"reviewed": {row.name: row.access_epoch for row in identities},
		"approved": [],
		"identities": identities,
	}


def confirm(reviewed: dict[str, str], approved: list[str]):
	frappe.only_for("System Manager")
	request = frappe.conf.get("passport_enrollment")
	if not frappe.conf.get("passport_disabled") or not isinstance(request, dict):
		raise frappe.ValidationError("Prepare this site's enrollment first.")
	operation = secrets.token_hex(24)
	frappe.db.commit()
	response = auth.call("/passport/registration", {"operation": operation})
	claims = auth.verify(
		response["registration"],
		"passport-registration+jwt",
		["sub", "operation", "installation", "redirect_uri"],
	)
	config = auth.config()
	if (
		claims["operation"] != operation
		or claims["sub"] != config.client_id
		or claims["redirect_uri"] != config.redirect_uri
		or not isinstance(claims["installation"], str)
		or not re.fullmatch(r"[a-f0-9]{32}", claims["installation"])
		or claims["exp"] - claims["iat"] > 60
		or request["public_jwk"] != protocol.public_jwk(config.private_key, config.kid)
	):
		raise frappe.ValidationError("Registration approval does not match this site.")
	admission.activate(reviewed, approved, installation=claims["installation"])
	return {"request": request, "installation": claims["installation"]}


if __name__ == "__main__":
	print(json.dumps(generate_key()))
