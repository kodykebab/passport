"""Live lease expiry, native timeout and delayed-renewal regressions."""

import json
import time

import frappe
from live_support import SITE, USER, check, db, get, login, renew, store

b, csrf, sid = login()
with db():
	row = store.get("federation", sid)
	check("lease capped at five minutes", 0 < row["exp"] - time.time() <= 300)
	before = frappe.db.sql("SELECT lastupdate FROM tabSessions WHERE sid=%s", sid)[0][0]
check("renew succeeds", renew(b, csrf).ok)
with db():
	after = frappe.db.sql("SELECT lastupdate FROM tabSessions WHERE sid=%s", sid)[0][0]
	check("renewal is not activity", before == after)
	row = store.get("federation", sid)
	row["exp"] = int(time.time()) - 1
	store.put("federation", sid, row)
check("HTTP blocks expired lease", get(b).status_code == 403)
with db():
	check("expired lease preserves native SQL session", bool(store.native(sid)))
check("recovery page remains reachable", b.get(SITE + "/passport/status").ok)
check("authoritative retry resumes lease", renew(b, csrf).ok and get(b).ok)
with db():
	row = store.get("federation", sid)
	row["exp"] = int(time.time()) - 1
	store.put("federation", sid, row)
# Wrong issuer authority is tested by deleting the associated issuer session, not waiting out an outage.
with db("id.localhost"):
	login_record = store.get("login", row["protocol_sid"])
	from frappe.sessions import delete_session

	delete_session(login_record["issuer_sid"], user=USER)
check("issuer native logout prevents expired lease recovery", not renew(b, csrf).ok)
check("failed recovery remains blocked", get(b).status_code == 403)
check(
	"local logout permitted after lease expiry",
	b.post(SITE + "/api/method/logout", headers={"X-Frappe-CSRF-Token": csrf}).ok,
)
with db():
	check("local logout leaves terminal federation state", store.get("federation", sid)["revoked"])
	check("native session removed", not store.native(sid))

# SQL fallback must honor native absolute deadline, not just Redis.
b, csrf, sid = login()
with db():
	from datetime import datetime, timezone

	raw = frappe.db.sql("SELECT sessiondata FROM tabSessions WHERE sid=%s", sid)[0][0]
	data = json.loads(raw)
	data["session_end"] = datetime.fromtimestamp(time.time() - 1, timezone.utc).isoformat()
	frappe.db.sql("UPDATE tabSessions SET sessiondata=%s WHERE sid=%s", (json.dumps(data), sid))
	frappe.cache.hdel("session", sid)
check(
	"SQL fallback rejects absolute expiry", get(b).status_code == 403 or get(b).json().get("message") != USER
)

b, csrf, sid = login()
with db():
	frappe.db.sql("UPDATE tabSessions SET lastupdate=DATE_SUB(NOW(), INTERVAL 30 DAY) WHERE sid=%s", sid)
check("idle expiry prevents renewal", not renew(b, csrf).ok)
print("Lease integration checks passed")

# Capture a real signed response, then commit logout/disablement before applying it.
from passport_client import auth, federation

for action in ("logout", "disable"):
	b, csrf, sid = login()
	with db():
		frappe.local.session = frappe._dict(sid=sid, user=USER, data=frappe._dict())
		original = auth.call

		def delayed(path, body):
			result = original(path, body)
			if action == "logout":
				from frappe.sessions import delete_session

				delete_session(sid, user=USER)
			else:
				doc = frappe.get_doc("User", USER)
				doc.enabled = 0
				doc.save(ignore_permissions=True)
				frappe.db.commit()
			return result

		auth.call = delayed
		try:
			try:
				federation.renew()
				raise AssertionError("Delayed renewal was accepted")
			except auth.PassportError:
				check("delayed response cannot undo " + action, True)
		finally:
			auth.call = original
			if action == "disable":
				doc = frappe.get_doc("User", USER)
				doc.enabled = 1
				doc.save(ignore_permissions=True)
	with db():
		check(
			action + " leaves durable revocation",
			store.get("federation", sid)["revoked"] or not store.native(sid),
		)
print("Delayed-response checks passed")
