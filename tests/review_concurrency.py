"""Real MariaDB snapshot and concurrent native-update regressions.

Uses uniquely named protocol/native fixtures; does not disable the seeded user.
"""

import json
import threading
import uuid
from types import SimpleNamespace

import frappe
from live_support import USER, check, db
from passport import account
from passport_client import federation
from passport_core import store


def snapshot_revocation(site, kind, hook):
	fixture = "review-" + uuid.uuid4().hex
	with db(site):
		store.put("user_generation", fixture, {"generation": 0, "user": fixture})
		frappe.db.commit()
		# Establish an old consistent snapshot before another connection commits.
		assert store.get(kind, fixture) is None
		connection = frappe.db.create_connection()
		try:
			row = {
				"user": fixture,
				"issuer_sid": fixture,
				"revoked": False,
				"revision": 0,
				"passport_generation": 0,
			}
			with connection.cursor() as cursor:
				cursor.execute(
					"INSERT INTO passport_state(kind,id,data,user,issuer_sid) VALUES(%s,%s,%s,%s,%s)",
					(kind, fixture, json.dumps(row), fixture, fixture),
				)
			connection.commit()
			assert store.get(kind, fixture) is None, "Test requires REPEATABLE READ"
			hook(SimpleNamespace(name=fixture, enabled=0))
			frappe.db.commit()
			assert store.generation(fixture) > row["passport_generation"]
			hook(SimpleNamespace(name=fixture, enabled=1))
			assert store.generation(fixture) > row["passport_generation"], (
				"Reenable must not revive authority"
			)
			check(site + " disablement fences records committed after its snapshot", True)
		finally:
			frappe.db.rollback()
			with connection.cursor() as cursor:
				cursor.execute("DELETE FROM passport_state WHERE id=%s", (fixture,))
			connection.commit()
			connection.close()


snapshot_revocation("site1.localhost", "federation", federation.user_changed)
snapshot_revocation("id.localhost", "login", account.user_changed)

# Reproduce the old User/Sessions cycle with real transactions. The update worker
# signals immediately before its first User SQL call; old code already held the
# Sessions lock at that point, while fixed code has not yet acquired it.
sid = "review-" + uuid.uuid4().hex
with db():
	now = frappe.utils.now()
	data = {
		"user": USER,
		"last_updated": now,
		"creation": now,
		"session_expiry": "240:00:00",
		"passport_generation": store.generation(USER),
	}
	frappe.db.sql(
		"INSERT INTO tabSessions(sid,user,sessiondata,lastupdate,status) VALUES(%s,%s,%s,%s,'Active')",
		(sid, USER, json.dumps(data), now),
	)

reached_user, finished = threading.Event(), threading.Event()
errors = []


def update_worker():
	try:
		with db():
			from frappe.sessions import Session

			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=5")
			original = frappe.db.sql

			def observed(query, *args, **kwargs):
				if "tabUser" in str(query):
					reached_user.set()
				return original(query, *args, **kwargs)

			frappe.db.sql = observed
			frappe.local.session = frappe._dict(user=USER, sid=sid)
			frappe.local.request_ip = "127.0.0.1"
			frappe.local.lang = "en"
			session = Session.__new__(Session)
			session.sid, session.user = sid, USER
			session._update_in_cache = False
			session.data = frappe._dict(user=USER, sid=sid, data=frappe._dict(data))
			assert session.update(force=True)
	except Exception as exc:
		errors.append(type(exc).__name__ + ": " + str(exc))
	finally:
		finished.set()


worker = threading.Thread(target=update_worker)
try:
	with db():
		frappe.db.sql("SET SESSION innodb_lock_wait_timeout=5")
		frappe.db.sql("SELECT name FROM tabUser WHERE name=%s FOR UPDATE", USER)
		worker.start()
		assert reached_user.wait(5), "Update did not reach the User boundary"
		assert store.get_valid_native_session(sid, USER, lock=True), (
			"Renewal validation must acquire native session"
		)
		frappe.db.commit()
	assert finished.wait(8), "Concurrent native update did not finish"
	worker.join()
	assert not errors, errors
	check("concurrent native update and renewal locking do not deadlock", True)
finally:
	worker.join(timeout=8)
	with db():
		frappe.db.sql("DELETE FROM tabSessions WHERE sid=%s", sid)
		frappe.cache.hdel("session", sid)
