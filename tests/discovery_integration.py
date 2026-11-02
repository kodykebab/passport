import threading
import uuid
from pathlib import Path
from unittest.mock import patch

import frappe
from passport import connections
from passport_client import auth, discovery, protocol, store


def run(user, connect, check):
	connect("client")
	subject = "outcome-race-" + uuid.uuid4().hex
	issuer = frappe.conf.passport_issuer
	sites = str(Path(frappe.local.sites_path).resolve())
	started, finished = threading.Event(), threading.Event()
	errors = []

	def deny():
		try:
			frappe.init(site="test.local", sites_path=sites)
			frappe.connect()
			started.set()
			discovery.record(issuer, subject, False)
			frappe.db.commit()
		except Exception as error:
			errors.append(type(error).__name__)
		finally:
			frappe.destroy()
			finished.set()

	thread = threading.Thread(target=deny)
	try:
		discovery.record(issuer, subject, True)
		thread.start()
		assert started.wait(5) and not finished.wait(0.2)
		frappe.db.commit()
		assert finished.wait(5) and not errors, errors
		race_key = protocol.digest((issuer + "\0" + subject).encode())
		race = store.get("discovery", race_key)
		check(
			race["sequence"] == 2 and not race["allowed"],
			"concurrent first outcomes serialize without losing denial",
		)
		frappe.db.sql("DELETE FROM passport_state WHERE kind='discovery' AND id=%s", race_key)
		frappe.db.commit()
	finally:
		frappe.db.rollback()
		thread.join(timeout=5)
	identity = frappe.get_doc("Passport Identity", {"user": user})
	key = protocol.digest((identity.issuer + "\0" + identity.subject).encode())
	allowed = store.get("discovery", key)
	check(allowed and allowed["allowed"], "successful final lease queues allowed discovery outcome")
	discovery.deliver()
	check(not store.get("discovery", key)["pending"], "scheduler acknowledges signed outcome delivery")
	connect("issuer")
	check(
		any(row["client"] == "acceptance-client" for row in connections.sites(user, picker=True)),
		"confirmed allowed site appears in picker",
	)
	connect("client")
	identity = frappe.get_doc("Passport Identity", {"user": user})
	identity.admitted = 0
	identity.save()
	frappe.db.commit()
	denied = store.get("discovery", key)
	check(
		denied["sequence"] > allowed["sequence"] and not denied["allowed"],
		"admission removal durably queues newer denial",
	)
	discovery.deliver()
	connect("issuer")
	check(bool(store.rows("login", user=user)), "historical login exists for fallback regression")
	check(not connections.sites(user, picker=True), "confirmed denial removes site despite historical login")
	check(bool(connections.sites(user)), "hidden site retains separate account consent management")
	connect("client")
	body = {field: allowed[field] for field in ("subject", "installation", "sequence", "allowed")}
	auth.call("/passport/site-access", body)
	connect("issuer")
	check(not connections.sites(user, picker=True), "delayed success cannot overwrite newer denial")
	connect("client")
	body = {field: denied[field] for field in ("subject", "installation", "sequence", "allowed")}
	auth.call("/passport/site-access", body)
	for invalid in (
		{**body, "subject": ""},
		{**body, "allowed": True},
		{**body, "sequence": True},
		{**body, "installation": "superseded"},
	):
		try:
			auth.call("/passport/site-access", invalid)
		except auth.PassportError:
			pass
		else:
			raise AssertionError("invalid discovery report accepted")
	check(True, "duplicates are idempotent; conflicts, invalid sequence and old installation are rejected")
	identity = frappe.get_doc("Passport Identity", {"user": user})
	identity.admitted = 1
	identity.save()
	discovery.record(identity.issuer, identity.subject, True)
	frappe.db.commit()
	real_call = auth.call

	def changed_during_ack(path, data):
		result = real_call(path, data)
		discovery.record(identity.issuer, identity.subject, False)
		return result

	with patch.object(auth, "call", side_effect=changed_during_ack):
		discovery.deliver()
	current = store.get("discovery", key)
	check(current["pending"] and not current["allowed"], "acknowledgment cannot erase a newer local outcome")
	with patch.object(auth, "call", side_effect=auth.PassportError("fixture outage")):
		discovery.deliver()
	check(store.get("discovery", key)["pending"], "outage retains pending outcome for scheduler retry")
	connect("issuer")
	check(
		bool(connections.sites(user, picker=True)), "outage does not fabricate remote denial before delivery"
	)
	connect("client")
	current = store.get("discovery", key)
	current["retry_at"] = 0
	store.put("discovery", key, current)
	frappe.db.commit()
	discovery.deliver()
	connect("issuer")
	check(not connections.sites(user, picker=True), "retry delivers confirmed loss after outage")
	connections.set_revoked(user, "acceptance-client", True)
	frappe.db.commit()
	connect("client")
	discovery.record(identity.issuer, identity.subject, True)
	frappe.db.commit()
	discovery.deliver()
	connect("issuer")
	row = connections.sites(user)[0]
	check(
		row["revoked"] and not row["login_url"],
		"allowed discovery does not override issuer consent revocation",
	)
	connections.set_revoked(user, "acceptance-client", False)
	frappe.db.commit()
	connect("client")
	identity = frappe.get_doc("Passport Identity", {"user": user})
	values = {field: identity.get(field) for field in ("issuer", "subject", "user")}
	frappe.delete_doc("Passport Identity", identity.name)
	frappe.db.commit()
	discovery.deliver()
	check(store.get("discovery", key) is not None, "unlink retains ordered denial tombstone")
	frappe.get_doc({"doctype": "Passport Identity", **values, "admitted": 1}).insert()
	frappe.db.commit()
