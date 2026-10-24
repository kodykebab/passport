"""Real SMTP outage/retry check. Explicitly stops only this project's local Mailpit."""

import argparse
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import frappe
import requests
from email_forms import Forms
from live_support import ID, ROOT, USER, BrowserSessions, check, db, store
from page_data import page_data

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mailpit-pid", type=int, required=True)
args = parser.parse_args()
command = Path(f"/proc/{args.mailpit_pid}/cmdline").read_bytes().split(b"\0")
assert str(ROOT / ".data/mailpit.db").encode() in command, "PID must be this project's Mailpit"
assert Path(f"/proc/{args.mailpit_pid}/exe").resolve() == ROOT / ".data/bin/mailpit"

MAIL = "http://127.0.0.1:8025"
with BrowserSessions() as sessions:
	attempts, messages = [], []
	mailpit_pid = args.mailpit_pid
	log = open(ROOT / ".data/logs/mailpit.log", "ab", buffering=0)

	def wait_for(fn, description, seconds=30):
		until = time.monotonic() + seconds
		while time.monotonic() < until:
			value = fn()
			if value:
				return value
			time.sleep(0.5)
		raise AssertionError(description)

	def available():
		try:
			return requests.get(MAIL + "/api/v1/info", timeout=1).ok
		except requests.ConnectionError:
			return False

	def stop_mail():
		global mailpit_pid
		os.kill(mailpit_pid, signal.SIGTERM)
		wait_for(lambda: not available(), "Mailpit did not stop")
		mailpit_pid = None

	def start_mail():
		global mailpit_pid
		if mailpit_pid is None:
			process = subprocess.Popen(
				[
					str(ROOT / ".data/bin/mailpit"),
					"--listen",
					"127.0.0.1:8025",
					"--smtp",
					"127.0.0.1:1025",
					"--database",
					str(ROOT / ".data/mailpit.db"),
				],
				stdin=subprocess.DEVNULL,
				stdout=log,
				stderr=log,
				start_new_session=True,
			)
			mailpit_pid = process.pid
		wait_for(available, "Mailpit did not start")

	def request_code():
		browser = sessions.browser()
		page = browser.get(ID + "/passport/email", timeout=10)
		form = Forms(page.text).action("send")
		attempts.append(form["attempt"])
		page = browser.post(ID + "/passport/email", data={**form, "email": USER}, timeout=10)
		check(
			"SMTP outage still gives generic queued feedback",
			page.ok and bool(page_data(page.text)["challenge"]),
		)
		return browser, form["attempt"], Forms(page.text).action("verify")

	def failed_delivery(attempt):
		with db("id.localhost"):
			row = store.get("email_challenge", attempt)
			return row if frappe.db.get_value("Email Queue", row["mail"][-1], "retry") else None

	def retry_delivery():
		# Native queue flushing leaves a ten-second undo window after creation.
		time.sleep(11)
		with db("id.localhost"):
			frappe.set_user("Administrator")
			frappe.enqueue("frappe.email.queue.flush", enqueue_after_commit=True)

	def delivered_code(attempt):
		for message in requests.get(MAIL + "/api/v1/messages", timeout=5).json()["messages"]:
			body = requests.get(MAIL + "/api/v1/message/" + message["ID"], timeout=5).json()
			if attempt[:8] in body["Text"]:
				messages.append(message["ID"])
				return re.search(r"\b[0-9]{6}\b", body["Text"]).group()

	try:
		for expired in (False, True):
			stop_mail()
			browser, attempt, form = request_code()
			original = wait_for(lambda: failed_delivery(attempt), "Worker did not record actual SMTP failure")
			check("real SMTP connection failure recorded in queue", True)
			if expired:
				with db("id.localhost"):
					row = store.get("email_challenge", attempt)
					row["expires"] = int(time.time()) - 1
					store.put("email_challenge", attempt, row)
			start_mail()
			retry_delivery()
			code = wait_for(lambda: delivered_code(attempt), "Queue retry did not deliver the message")
			with db("id.localhost"):
				row = store.get("email_challenge", attempt)
				check("SMTP retry never extends code expiry", row["expires"] <= original["expires"])
			response = browser.post(
				ID + "/passport/email", data={**form, "code": code}, allow_redirects=False, timeout=10
			)
			if expired:
				check(
					"delayed email cannot authenticate after controlled expiry",
					response.status_code == 400 and "expired" in response.text,
				)
			else:
				check("delivery recovery permits login before original expiry", response.status_code == 303)
	finally:
		start_mail()
		with db("id.localhost"):
			for attempt in attempts:
				row = store.get("email_challenge", attempt)
				if row:
					for name in row["mail"]:
						frappe.db.delete("Email Queue Recipient", {"parent": name})
						frappe.db.delete("Email Queue", {"name": name})
					frappe.db.sql(
						"DELETE FROM passport_state WHERE kind='email_challenge' AND id=%s", attempt
					)
		for message in set(messages):
			requests.delete(MAIL + "/api/v1/messages", json={"IDs": [message]}, timeout=5)
		log.close()
	print("PASS delivery fixtures cleaned up; Mailpit remains running")
