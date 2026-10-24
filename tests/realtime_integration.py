"""Live Socket.IO deadline and admission check using a shortened test lease."""

import json
import shutil
import subprocess
import time

import frappe
from frappe.sessions import delete_session
from live_support import ROOT, USER, check, db, login, renew, store

node = shutil.which("node")
if not node:
	raise RuntimeError("Node.js is required for realtime integration tests")

browser, csrf, sid = login()
try:
	with db():
		port = frappe.conf.socketio_port
		row = store.get("federation", sid)
		row["exp"] = int(time.time()) + 10
		store.put("federation", sid, row)

	for reject in (False, True):
		r = subprocess.run(
			[node, str(ROOT / "tests/realtime_probe.cjs")],
			input=json.dumps({"sid": sid, "reject": reject, "port": port}),
			text=True,
			capture_output=True,
			timeout=20,
		)
		print(r.stdout, end="")
		if r.stderr:
			print(r.stderr)
		check("socket probe passed", r.returncode == 0)
		if not reject:
			time.sleep(2)
	check("authoritative renewal after socket expiry", renew(browser, csrf).ok)
finally:
	for site in ("id.localhost", "site1.localhost"):
		session_id = browser.cookies.get("sid", domain=site)
		with db(site):
			if (
				session_id
				and session_id != "Guest"
				and frappe.db.exists("Sessions", {"sid": session_id, "user": USER})
			):
				delete_session(session_id, user=USER, reason="Realtime integration test cleanup")
