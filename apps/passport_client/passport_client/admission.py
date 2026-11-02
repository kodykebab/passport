"""Site-owned admission; activation locks identities before the policy row."""

import hashlib
import json
import secrets

import frappe

from passport_client import store

POLICY_KIND = "admission_policy"
POLICY_ID = "site"
MIGRATION = "passport_client.patches.admission_policy"


def after_install():
	store.install()
	if not store.get(POLICY_KIND, POLICY_ID, lock=True):
		store.create(POLICY_KIND, POLICY_ID, {"mode": "enforced"})


def initialize_existing():
	store.install()
	if store.get(POLICY_KIND, POLICY_ID, lock=True):
		return
	if frappe.db.exists("Patch Log", {"patch": MIGRATION, "skipped": 0}):
		raise frappe.ValidationError("Admission policy is missing after migration. Restore it explicitly.")
	store.create(POLICY_KIND, POLICY_ID, {"mode": "compatibility"})


def permits(row: dict, lock: bool = False) -> bool:
	if not row.get("association_id") or not row.get("access_epoch"):
		return False
	return bool(
		frappe.db.sql(
			"""SELECT identity.name FROM `tabPassport Identity` identity
		STRAIGHT_JOIN passport_state policy ON policy.kind=%s AND policy.id=%s
		WHERE identity.name=%s AND identity.access_epoch=%s
		AND identity.issuer=%s AND identity.subject=%s AND identity.user=%s
		AND (JSON_UNQUOTE(JSON_EXTRACT(policy.data, '$.mode'))='compatibility'
		OR (JSON_UNQUOTE(JSON_EXTRACT(policy.data, '$.mode'))='enforced' AND identity.admitted=1))
		AND (%s IS NULL OR JSON_UNQUOTE(JSON_EXTRACT(policy.data, '$.installation'))=%s)"""
			+ (" FOR UPDATE" if lock else ""),
			(
				POLICY_KIND,
				POLICY_ID,
				row["association_id"],
				row["access_epoch"],
				row["issuer"],
				row["sub"],
				row["user"],
				frappe.conf.get("passport_installation"),
				frappe.conf.get("passport_installation"),
			),
		)
	)


def preview() -> dict[str, str]:
	frappe.only_for("System Manager")
	return dict(frappe.db.sql("SELECT name,access_epoch FROM `tabPassport Identity` ORDER BY name"))


def activate(reviewed: dict[str, str], approved: list[str], installation: str | None = None) -> None:
	frappe.only_for("System Manager")
	if not isinstance(reviewed, dict) or not isinstance(approved, list):
		raise frappe.ValidationError("Review the identity associations before activation.")
	if any(not isinstance(key, str) or not isinstance(value, str) for key, value in reviewed.items()):
		raise frappe.ValidationError("Invalid reviewed association.")
	if any(not isinstance(name, str) or name not in reviewed for name in approved):
		raise frappe.ValidationError("Approval must refer to a reviewed association.")
	selection = set(approved)
	request = [reviewed, sorted(selection)]
	if installation:
		request.append(installation)
	receipt = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
	current = dict(
		frappe.db.sql("SELECT name,access_epoch FROM `tabPassport Identity` ORDER BY name FOR UPDATE")
	)
	policy = store.get(POLICY_KIND, POLICY_ID, lock=True)
	if policy and policy.get("mode") == "enforced" and policy.get("activation_request") == receipt:
		return
	if not policy or policy.get("mode") not in (
		("compatibility", "enforced") if installation else ("compatibility",)
	):
		raise frappe.ValidationError("Admission policy cannot be activated from this state.")
	if current != reviewed:
		raise frappe.ValidationError("Identity associations changed. Review them again.")
	for name in current:
		identity = frappe.get_doc("Passport Identity", name)
		identity.admitted = int(name in selection)
		identity.save(ignore_version=False)
		if identity.access_epoch == current[name]:
			frappe.db.set_value("Passport Identity", name, "access_epoch", secrets.token_hex(16))
	store.put(
		POLICY_KIND,
		POLICY_ID,
		{
			"mode": "enforced",
			"activated_by": frappe.session.user,
			"activated_at": str(frappe.utils.now_datetime()),
			"activation_request": receipt,
			**({"installation": installation} if installation else {}),
		},
	)
	if installation:
		from passport_client import discovery

		for issuer, subject in frappe.db.sql("SELECT issuer,subject FROM `tabPassport Identity`"):
			discovery.record(issuer, subject, False, installation=installation)
