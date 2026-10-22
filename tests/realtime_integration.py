"""Live Socket.IO deadline and admission check using a shortened test lease."""

import json
import subprocess
import time

from live_support import ROOT, check, db, login, renew, store

b, csrf, sid = login()
with db():
	row = store.get("federation", sid)
	row["exp"] = int(time.time()) + 10
	store.put("federation", sid, row)

for reject in (False, True):
	r = subprocess.run(
		["/opt/homebrew/opt/node@26/bin/node", str(ROOT / "tests/realtime_probe.cjs")],
		input=json.dumps({"sid": sid, "reject": reject}),
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
check("authoritative renewal after socket expiry", renew(b, csrf).ok)
