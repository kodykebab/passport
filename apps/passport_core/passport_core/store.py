"""Durable protocol records. Mutations serialize on User before session/state rows."""

import json
import time
from datetime import datetime, timezone

import frappe


def install():
	frappe.db.commit()
	frappe.db.sql("""CREATE TABLE IF NOT EXISTS passport_state (
		kind varchar(32) NOT NULL, id varchar(140) NOT NULL,
		data longtext NOT NULL, PRIMARY KEY(kind,id)
	) ENGINE=InnoDB""")
	# Idempotent migration of existing PoC records. Do not discard identity/session state.
	frappe.db.sql("""ALTER TABLE passport_state
		ADD COLUMN IF NOT EXISTS user varchar(140) NULL,
		ADD COLUMN IF NOT EXISTS issuer_sid varchar(140) NULL,
		ADD INDEX IF NOT EXISTS passport_state_user (kind, user),
		ADD INDEX IF NOT EXISTS passport_state_issuer_session (kind, issuer_sid)""")
	frappe.db.sql("""UPDATE passport_state SET
		user = JSON_UNQUOTE(JSON_EXTRACT(data, '$.user')),
		issuer_sid = JSON_UNQUOTE(JSON_EXTRACT(data, '$.issuer_sid'))""")
	frappe.db.sql("""INSERT IGNORE INTO passport_state(kind,id,data,user,issuer_sid)
		SELECT 'user_generation', name, JSON_OBJECT('generation', 0, 'user', name), name, NULL FROM tabUser""")
	frappe.db.commit()


def generation(user, lock=False):
	state = get("user_generation", user, lock=lock)
	return state["generation"] if state else 0


def revoke_user(user):
	# User is the serialization boundary shared with native creation/renewal.
	frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", user)
	current = generation(user, lock=True)
	put("user_generation", user, {"user": user, "generation": current + 1})


def get(kind, key, lock=False):
	result = frappe.db.sql(
		"SELECT data FROM passport_state WHERE kind=%s AND id=%s" + (" FOR UPDATE" if lock else ""),
		(kind, key),
	)
	return json.loads(result[0][0]) if result else None


def values(kind, key, data):
	return kind, key, json.dumps(data), data.get("user"), data.get("issuer_sid")


def put(kind, key, data):
	frappe.db.sql(
		"""INSERT INTO passport_state(kind,id,data,user,issuer_sid) VALUES(%s,%s,%s,%s,%s)
		ON DUPLICATE KEY UPDATE data=VALUES(data), user=VALUES(user), issuer_sid=VALUES(issuer_sid)""",
		values(kind, key, data),
	)


def create(kind, key, data):
	try:
		frappe.db.sql(
			"INSERT INTO passport_state(kind,id,data,user,issuer_sid) VALUES(%s,%s,%s,%s,%s)",
			values(kind, key, data),
		)
	except Exception as exc:
		if frappe.db.is_duplicate_entry(exc):
			raise ValueError("Protocol record already exists") from exc
		raise


def rows(kind, *, user=None, issuer_sid=None, lock=False):
	conditions, parameters = ["kind=%s"], [kind]
	if user is not None:
		conditions.append("user=%s")
		parameters.append(user)
	if issuer_sid is not None:
		conditions.append("issuer_sid=%s")
		parameters.append(issuer_sid)
	query = "SELECT id,data FROM passport_state WHERE " + " AND ".join(conditions) + " ORDER BY id"
	if lock:
		query += " FOR UPDATE"
	return [(key, json.loads(data)) for key, data in frappe.db.sql(query, parameters)]


def get_valid_native_session(sid, user=None, lock=False):
	from frappe.sessions import get_expiry_in_seconds

	if lock and not user:
		raise ValueError("Locking session validation requires its expected user")
	if lock:
		# Current read, not a possibly older repeatable-read snapshot.
		enabled = frappe.db.sql("SELECT enabled FROM tabUser WHERE name=%s FOR UPDATE", user)
		if not enabled or not enabled[0][0]:
			return None
	result = frappe.db.sql(
		"SELECT user,sessiondata,lastupdate FROM tabSessions WHERE sid=%s" + (" FOR UPDATE" if lock else ""),
		sid,
	)
	if not result:
		return None
	who, raw, updated = result[0]
	data = json.loads(raw)
	if user and who != user:
		return None
	if data.get("passport_generation", 0) != generation(who, lock=lock):
		return None
	if not lock and not frappe.db.get_value("User", who, "enabled"):
		return None
	idle = min(get_expiry_in_seconds(data.get("session_expiry")), get_expiry_in_seconds())
	remaining = idle - frappe.utils.time_diff_in_seconds(frappe.utils.now_datetime(), updated)
	if remaining <= 0:
		return None
	if data.get("session_end"):
		end = datetime.fromisoformat(data["session_end"])
		if end.tzinfo is None:
			end = end.replace(tzinfo=timezone.utc)
		if end.timestamp() <= time.time():
			return None
	data["user"] = who
	data["idle_remaining"] = remaining
	return data
