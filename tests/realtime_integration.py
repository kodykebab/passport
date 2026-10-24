"""Live Socket.IO deadline and admission check using a shortened test lease."""

import json
import shutil
import subprocess
import time

import frappe
from live_support import ROOT, BrowserSessions, check, db, renew, store

node = shutil.which("node")
if not node:
	raise RuntimeError("Node.js is required for realtime integration tests")

with BrowserSessions() as sessions:
	browser, csrf, sid = sessions.login()
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
