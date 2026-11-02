import time

import frappe

from passport_client import auth, protocol, store


def get_status():
	from frappe.utils.scheduler import is_scheduler_inactive

	frappe.only_for("System Manager")
	installation = frappe.conf.get("passport_installation")
	policy = store.get("admission_policy", "site") or {}
	pending, retrying = frappe.db.sql(
		"""SELECT COUNT(*), COALESCE(SUM(JSON_EXTRACT(data,'$.retry_at')>0),0)
		FROM passport_state WHERE kind='discovery' AND JSON_EXTRACT(data,'$.pending')=true
		AND JSON_UNQUOTE(JSON_EXTRACT(data,'$.installation'))=%s""",
		installation,
	)[0]
	return {
		"enrolled": bool(installation)
		and policy.get("installation") == installation
		and not frappe.conf.get("passport_enrollment"),
		"suspended": bool(frappe.conf.get("passport_disabled")),
		"scheduler_enabled": not is_scheduler_inactive(verbose=False),
		"pending": int(pending),
		"retrying": int(retrying),
	}


def record(issuer: str, subject: str, allowed: bool, *, installation: str | None = None):
	installation = installation or frappe.conf.get("passport_installation")
	if not installation or issuer != frappe.conf.get("passport_issuer"):
		return
	if frappe.conf.get("passport_disabled") and installation == frappe.conf.get("passport_installation"):
		return
	key = protocol.digest((issuer + "\0" + subject).encode())
	frappe.db.sql(
		"""INSERT INTO passport_state(kind,id,data) VALUES('discovery',%s,'{}')
		ON DUPLICATE KEY UPDATE id=VALUES(id)""",
		key,
	)
	previous = store.get("discovery", key, lock=True) or {}
	if previous.get("installation") == installation and previous.get("allowed") is allowed:
		return
	sequence = previous.get("sequence", 0) + 1 if previous.get("installation") == installation else 1
	store.put(
		"discovery",
		key,
		{
			"issuer": issuer,
			"subject": subject,
			"installation": installation,
			"sequence": sequence,
			"allowed": allowed,
			"pending": True,
			"retry_at": 0,
		},
	)


def deliver():
	installation = frappe.conf.get("passport_installation")
	if not installation or frappe.conf.get("passport_disabled"):
		return
	keys = frappe.db.sql(
		"""SELECT id FROM passport_state WHERE kind='discovery'
		AND JSON_EXTRACT(data,'$.pending')=true
		AND JSON_UNQUOTE(JSON_EXTRACT(data,'$.installation'))=%s
		AND JSON_EXTRACT(data,'$.retry_at')<=%s
		ORDER BY JSON_EXTRACT(data,'$.retry_at'),id LIMIT 20""",
		(installation, time.time()),
	)
	for (key,) in keys:
		row = store.get("discovery", key)
		frappe.db.commit()
		if not row or not row["pending"] or row["installation"] != installation:
			continue
		body = {field: row[field] for field in ("subject", "installation", "sequence", "allowed")}
		try:
			acknowledgment = auth.call("/passport/site-access", body)
			delivered = acknowledgment == {"installation": installation, "sequence": row["sequence"]}
		except auth.PassportError:
			delivered = False
		current = store.get("discovery", key, lock=True)
		if current and (current["installation"], current["sequence"]) == (installation, row["sequence"]):
			current.update(pending=not delivered, retry_at=0 if delivered else time.time() + 60)
			store.put("discovery", key, current)
		frappe.db.commit()
