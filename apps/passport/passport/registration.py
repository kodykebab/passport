import re
import time

import frappe


def check(client, expected):
	"""Recheck registration after user/session locks and hold it until protocol commit."""
	rows = frappe.db.sql(
		"""SELECT enabled,registration_revision FROM `tabPassport Client`
		WHERE name=%s FOR UPDATE""",
		client,
	)
	if not rows or not rows[0][0] or (rows[0][1] or "") != (expected or ""):
		raise ValueError("client_registration_changed")
	return rows[0][1] or ""


def status(client, body):
	from passport import oidc

	operation = body.get("operation")
	if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{48}", operation):
		raise ValueError("invalid_operation")
	revision = check(client.name, client.registration_revision)
	if not revision:
		raise ValueError("registration_requires_approval")
	now = int(time.time())
	return {
		"registration": oidc.signing(
			{
				"iss": oidc.issuer(),
				"aud": client.name,
				"sub": client.name,
				"iat": now,
				"exp": now + 60,
				"operation": operation,
				"installation": revision,
				"redirect_uri": client.redirect_uri,
			},
			"passport-registration+jwt",
		)
	}
