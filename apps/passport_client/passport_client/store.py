"""Shared durable Passport state helpers."""

import secrets

import frappe
from passport_core.store import (
	create,
	generation,
	get,
	get_valid_native_session,
	put,
	revoke_user,
	rows,
)
from passport_core.store import (
	install as install_core,
)


def install():
	install_core()
	for (name,) in frappe.db.sql(
		"SELECT name FROM `tabPassport Identity` WHERE access_epoch IS NULL OR access_epoch='' FOR UPDATE"
	):
		frappe.db.sql(
			"UPDATE `tabPassport Identity` SET access_epoch=%s WHERE name=%s",
			(secrets.token_hex(16), name),
		)
	frappe.db.commit()


__all__ = ["create", "generation", "get", "install", "get_valid_native_session", "put", "revoke_user", "rows"]
