import json
import re
import threading
import time
import unittest
import uuid
from email import message_from_string
from html import unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from passport_client import auth, invitations, protocol, store
from passport_core.store import native_session_id
from werkzeug.wrappers import Request


class InvitationTests(unittest.TestCase):
	def setUp(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		self.previous_policy = store.get("admission_policy", "site")
		self.user = f"invitation-{uuid.uuid4().hex}@example.invalid"
		self.other_users = []
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.user,
				"first_name": "Invitation Fixture",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(self.cleanup)

	def cleanup(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		for doctype in ("Passport Invitation", "Passport Identity"):
			for name in frappe.get_all(doctype, filters={"user": self.user}, pluck="name"):
				if doctype == "Passport Invitation":
					for queue in frappe.get_all(
						"Email Queue",
						filters={"reference_doctype": doctype, "reference_name": name},
						pluck="name",
					):
						frappe.delete_doc(
							"Email Queue", queue, ignore_permissions=True, delete_permanently=True
						)
				frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True)
		frappe.db.sql("DELETE FROM tabSessions WHERE user=%s", self.user)
		if frappe.db.exists("User", self.user):
			frappe.delete_doc("User", self.user, ignore_permissions=True, delete_permanently=True)
		for user in self.other_users:
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, ignore_permissions=True, delete_permanently=True)
			frappe.db.sql("DELETE FROM passport_state WHERE user=%s", user)
		frappe.db.sql("DELETE FROM passport_state WHERE user=%s", self.user)
		if self.previous_policy is None:
			frappe.db.sql("DELETE FROM passport_state WHERE kind='admission_policy' AND id='site'")
		else:
			store.put("admission_policy", "site", self.previous_policy)
		frappe.db.commit()

	def signed_callback(self, *, existing=True, wrong_subject=False, wrong_purpose=False, cancelled=False):
		name, identity, seeded_attempt, secret, browser, sid = self.prepare_acceptance()
		frappe.db.sql("DELETE FROM passport_state WHERE kind='attempt' AND id=%s", seeded_attempt)
		if not existing:
			frappe.delete_doc("Passport Identity", identity.name, ignore_permissions=True)
		key = (
			Ed25519PrivateKey.generate()
			.private_bytes(
				serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
			)
			.decode()
		)
		settings = {
			"passport_allow_linking": True,
			"passport_disabled": False,
			"passport_issuer": identity.issuer,
			"passport_client_id": "fixture",
			"passport_private_key": key,
			"host_name": "https://test.local",
		}
		request = Request.from_values(
			base_url="https://test.local", headers={"Cookie": "passport_browser=" + browser}
		)
		with (
			patch.dict(frappe.conf, settings),
			patch.object(frappe.local, "request", request, create=True),
			patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})),
		):
			with patch("passport_core.password.confirm_password") as password:
				response = invitations.start(name, secret, "fixture-password")
				password.assert_called_once_with(self.user, "fixture-password")
			state = parse_qs(urlparse(response.location).query)["state"][0]
			attempt_id = protocol.digest(state.encode())
			attempt = store.get("attempt", attempt_id)
			if cancelled:
				frappe.db.set_value("Passport Invitation", name, "status", "Cancelled")
				frappe.db.commit()
			now = int(time.time())
			claims = {
				"iss": identity.issuer,
				"aud": "fixture",
				"sub": "wrong" if wrong_subject else identity.subject,
				"sid": "issuer-fixture-session",
				"nonce": attempt["nonce"],
				"auth_time": now,
				"amr": ["pwd"],
				"passport_auth_method": "password",
				"issuer_session_expires_at": now + 600,
				"iat": now,
				"exp": now + 60,
				"email": "different-passport-address@example.invalid",
			}
			token = protocol.sign(
				claims, key, "fixture", "passport-id+jwt" if wrong_purpose else "passport-link+jwt"
			)
			frappe.cache.set_value("passport:issuer-jwk", protocol.public_jwk(key, "fixture"))
			callback = Request.from_values(
				path="/passport/callback?state=" + state + "&code=fixture",
				base_url="https://test.local",
				headers={"Cookie": "passport_browser=" + browser},
			)
			with (
				patch.object(frappe.local, "request", callback),
				patch.object(auth, "call", return_value={"id_token": token}),
			):
				if cancelled:
					self.assertEqual(auth.response().status_code, 403)
					self.assertFalse(store.get("attempt", attempt_id).get("verified"))
					self.assertFalse(frappe.db.exists("Passport Identity", {"user": self.user}))
					return
				if wrong_subject or wrong_purpose:
					with self.assertRaises((auth.PassportError, ValueError)):
						auth.callback()
					self.assertFalse(store.get("attempt", attempt_id).get("verified"))
					return
				self.assertEqual(auth.callback().status_code, 303)
				with self.assertRaises(auth.PassportError):
					auth.callback()
			verified = store.get("attempt", attempt_id)["verified"]
			self.assertEqual(verified["exp"], claims["exp"])
			self.assertNotIn("email", verified)
			self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Pending")
			self.assertEqual(
				frappe.db.get_value("Passport Identity", verified["association_id"], "admitted"), 0
			)
			self.assertEqual(invitations.accept(attempt_id, secret, browser), verified["association_id"])

	def test_signed_callback_confirms_existing_link_with_different_email(self):
		self.signed_callback()

	def test_signed_callback_creates_unapproved_link_before_acceptance(self):
		self.signed_callback(existing=False)

	def test_signed_callback_rejects_subject_swap(self):
		self.signed_callback(wrong_subject=True)

	def test_signed_callback_rejects_login_token_as_link_proof(self):
		self.signed_callback(wrong_purpose=True)

	def test_cancelled_callback_rolls_back_new_link(self):
		self.signed_callback(existing=False, cancelled=True)

	def concurrent_mutation(self, kind):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		if kind == "role":
			manager = "manager-" + self.user
			self.other_users.append(manager)
			frappe.get_doc(
				{
					"doctype": "User",
					"email": manager,
					"first_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"role": "System Manager"}],
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value("Passport Invitation", name, "inviter", manager)
		frappe.db.commit()
		ready, release = threading.Event(), threading.Event()
		outcome = []

		def writer():
			frappe.init(site="test.local", sites_path="/tmp/passport-acceptance-20261029/sites")
			frappe.connect()
			frappe.set_user("Administrator")
			try:
				if kind == "cancel":
					invitations.cancel(name)
				elif kind == "identity":
					doc = frappe.get_doc("Passport Identity", identity.name)
					doc.subject = "changed-subject"
					doc.save()
				elif kind == "session":
					from frappe.sessions import delete_session

					native_commit = frappe.db.commit

					def commit_after_release(*args, **kwargs):
						ready.set()
						if not release.wait(10):
							raise TimeoutError("Session deletion was not released")
						return native_commit(*args, **kwargs)

					with patch.object(frappe.local.db, "commit", side_effect=commit_after_release):
						delete_session(sid, user=self.user)
				elif kind == "role":
					doc = frappe.get_doc("User", manager)
					doc.roles = [row for row in doc.roles if row.role != "System Manager"]
					doc.save()
				ready.set()
				if not release.wait(10):
					raise TimeoutError("Mutation was not released")
				frappe.db.commit()
				outcome.append("committed")
			except Exception as error:
				outcome.append(error)
				ready.set()
			finally:
				frappe.db.rollback()
				frappe.destroy()

		worker = threading.Thread(target=writer)
		worker.start()
		previous_timeout = frappe.db.sql("SELECT @@innodb_lock_wait_timeout")[0][0]
		try:
			self.assertTrue(ready.wait(10))
			self.assertEqual(outcome, [])
			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=1")
			with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
				with self.assertRaises(frappe.QueryTimeoutError):
					invitations.accept(attempt, secret, browser)
			frappe.db.rollback()
		finally:
			release.set()
			worker.join(timeout=10)
			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=%s", previous_timeout)
		self.assertFalse(worker.is_alive())
		self.assertEqual(outcome, ["committed"])
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			with self.assertRaises(frappe.PermissionError):
				invitations.accept(attempt, secret, browser)
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)

	def test_acceptance_serializes_with_cancellation(self):
		self.concurrent_mutation("cancel")

	def test_acceptance_serializes_with_identity_change(self):
		self.concurrent_mutation("identity")

	def test_acceptance_serializes_with_session_deletion(self):
		self.concurrent_mutation("session")

	def test_acceptance_serializes_with_inviter_role_removal(self):
		self.concurrent_mutation("role")

	def test_simultaneous_acceptance_grants_once(self):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		frappe.db.commit()
		ready = threading.Barrier(2)
		outcome = []

		def acceptor():
			frappe.init(site="test.local", sites_path="/tmp/passport-acceptance-20261029/sites")
			frappe.connect()
			frappe.local.session = frappe._dict(user=self.user, sid=sid, data={})
			try:
				ready.wait(timeout=10)
				result = invitations.accept(attempt, secret, browser)
				frappe.db.commit()
				outcome.append(result)
			except Exception as error:
				outcome.append(error)
			finally:
				frappe.db.rollback()
				frappe.destroy()

		workers = [threading.Thread(target=acceptor) for _ in range(2)]
		for worker in workers:
			worker.start()
		for worker in workers:
			worker.join(timeout=15)
			self.assertFalse(worker.is_alive())
		self.assertEqual(outcome, [identity.name, identity.name])
		for doctype, document in (("Passport Identity", identity.name), ("Passport Invitation", name)):
			self.assertEqual(frappe.db.count("Version", {"ref_doctype": doctype, "docname": document}), 1)

	def prepare_acceptance(self):
		name, secret = invitations.issue(self.user)
		identity = frappe.get_doc(
			{
				"doctype": "Passport Identity",
				"issuer": "https://issuer.example.invalid",
				"subject": uuid.uuid4().hex,
				"user": self.user,
			}
		).insert(ignore_permissions=True)
		sid, attempt_id, browser = (uuid.uuid4().hex for _ in range(3))
		frappe.db.sql(
			"INSERT INTO tabSessions(sid,user,sessiondata,lastupdate,status) VALUES(%s,%s,%s,NOW(),'Active')",
			(
				native_session_id(sid),
				self.user,
				json.dumps(
					{"session_expiry": "06:00:00", "passport_generation": store.generation(self.user)}
				),
			),
		)
		attempt = {
			"invitation": name,
			"invitation_digest": protocol.digest(secret.encode()),
			"action": "link",
			"spent": True,
			"user": self.user,
			"local_sid": native_session_id(sid),
			"generation": store.generation(self.user),
			"browser": protocol.digest(browser.encode()),
			"exp": time.time() + 600,
			"verified": {
				"association_id": identity.name,
				"access_epoch": identity.access_epoch,
				"issuer": identity.issuer,
				"subject": identity.subject,
				"exp": time.time() + 60,
			},
		}
		store.create("attempt", attempt_id, attempt)
		store.put("admission_policy", "site", {"mode": "enforced"})
		return name, identity, attempt_id, secret, browser, sid

	def test_acceptance_is_atomic_audited_and_idempotent(self):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			self.assertEqual(invitations.accept(attempt, secret, browser), identity.name)
			self.assertEqual(invitations.accept(attempt, secret, browser), identity.name)
		identity.reload()
		self.assertEqual(identity.admitted, 1)
		invitation = frappe.get_doc("Passport Invitation", name)
		self.assertEqual(invitation.status, "Accepted")
		self.assertEqual(invitation.accepted_by, self.user)
		for doctype, docname in (("Passport Identity", identity.name), ("Passport Invitation", name)):
			self.assertEqual(frappe.db.count("Version", {"ref_doctype": doctype, "docname": docname}), 1)
		identity.admitted = 0
		identity.save(ignore_permissions=True)
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			invitations.accept(attempt, secret, browser)
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)

	def test_acceptance_failure_rolls_back_grant_and_audit(self):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		from passport_client.passport_client.doctype.passport_invitation.passport_invitation import (
			PassportInvitation,
		)

		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			with patch.object(
				PassportInvitation, "on_update", create=True, side_effect=RuntimeError("fixture failure")
			):
				with self.assertRaises(RuntimeError):
					invitations.accept(attempt, secret, browser)
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Pending")
		self.assertFalse(
			frappe.db.exists("Version", {"ref_doctype": "Passport Identity", "docname": identity.name})
		)

	def test_successful_acceptance_leaves_commit_to_caller(self):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		frappe.db.savepoint("acceptance_caller")
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			invitations.accept(attempt, secret, browser)
		frappe.db.rollback(save_point="acceptance_caller")
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Pending")

	def test_invalid_proofs_cannot_grant(self):
		name, identity, attempt_id, secret, browser, sid = self.prepare_acceptance()
		original = store.get("attempt", attempt_id)
		for field, value in (
			("spent", False),
			("exp", 0),
			("generation", -1),
			("local_sid", "wrong"),
			("verified", None),
		):
			with self.subTest(field=field):
				store.put("attempt", attempt_id, {**original, field: value})
				with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
					with self.assertRaises(frappe.PermissionError):
						invitations.accept(attempt_id, secret, browser)
		store.put("attempt", attempt_id, original)
		for field, value in (("exp", 0), ("subject", "wrong"), ("issuer", "https://wrong.invalid")):
			with self.subTest(proof_field=field):
				store.put(
					"attempt", attempt_id, {**original, "verified": {**original["verified"], field: value}}
				)
				with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
					with self.assertRaises(frappe.PermissionError):
						invitations.accept(attempt_id, secret, browser)
		store.put("attempt", attempt_id, original)
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			for supplied_secret, supplied_browser in (("wrong", browser), (secret, "wrong")):
				with self.assertRaises(frappe.PermissionError):
					invitations.accept(attempt_id, supplied_secret, supplied_browser)
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)

	def test_forged_document_capability_cannot_grant(self):
		_, identity, _, _, _, sid = self.prepare_acceptance()
		identity.admitted = 1
		identity._passport_acceptance = {"approved": True}
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			with self.assertRaises(frappe.PermissionError):
				identity.save(ignore_permissions=True)

	def test_current_authority_changes_reject_acceptance(self):
		name, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		changes = (
			("Passport Identity", identity.name, "access_epoch", "changed"),
			("Passport Identity", identity.name, "subject", "another-subject"),
			("Passport Invitation", name, "status", "Cancelled"),
			("Passport Invitation", name, "expires_at", "2000-01-01 00:00:00"),
			("User", self.user, "enabled", 0),
			("User", self.user, "email", "changed@example.invalid"),
		)
		for doctype, document, field, value in changes:
			with self.subTest(doctype=doctype, field=field):
				frappe.db.savepoint("authority_test")
				frappe.db.set_value(doctype, document, field, value)
				with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
					with self.assertRaises(frappe.PermissionError):
						invitations.accept(attempt, secret, browser)
				frappe.db.rollback(save_point="authority_test")
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)

	def test_inviter_role_removal_rejects_acceptance(self):
		inviter = "manager-" + self.user
		frappe.get_doc(
			{
				"doctype": "User",
				"email": inviter,
				"first_name": "Inviter Fixture",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
		with patch.object(frappe.local, "session", frappe._dict(user=inviter, sid="fixture", data={})):
			_, identity, attempt, secret, browser, sid = self.prepare_acceptance()
		frappe.db.sql("DELETE FROM `tabHas Role` WHERE parent=%s AND role='System Manager'", inviter)
		with patch.object(frappe.local, "session", frappe._dict(user=self.user, sid=sid, data={})):
			with self.assertRaises(frappe.PermissionError):
				invitations.accept(attempt, secret, browser)
		self.assertEqual(frappe.db.get_value("Passport Identity", identity.name, "admitted"), 0)

	def test_issue_stores_only_digest_and_does_not_grant(self):
		name, secret = invitations.issue(self.user)
		doc = frappe.get_doc("Passport Invitation", name)
		self.assertEqual(doc.token_digest, protocol.digest(secret.encode()))
		self.assertNotIn(secret, doc.as_json())
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(doc.recipient_email, self.user)
		self.assertFalse(frappe.db.exists("Passport Identity", {"user": self.user}))

	def test_cancel_is_idempotent_and_audited(self):
		name, _ = invitations.issue(self.user)
		invitations.cancel(name)
		invitations.cancel(name)
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Cancelled")
		self.assertEqual(
			frappe.db.count("Version", {"ref_doctype": "Passport Invitation", "docname": name}), 1
		)

	def test_recipient_cannot_issue_or_cancel(self):
		name, _ = invitations.issue(self.user)
		frappe.set_user(self.user)
		with self.assertRaises(frappe.PermissionError):
			invitations.issue(self.user)
		with self.assertRaises(frappe.PermissionError):
			invitations.cancel(name)

	def test_ignore_permissions_does_not_bypass_authority(self):
		name, _ = invitations.issue(self.user)
		doc = frappe.get_doc("Passport Invitation", name)
		frappe.set_user(self.user)
		doc.status = "Cancelled"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)

	def test_terms_cannot_change(self):
		name, _ = invitations.issue(self.user)
		for field, value in {
			"recipient_email": "another@example.invalid",
			"user": "Administrator",
			"inviter": self.user,
			"token_digest": "a" * 64,
			"expires_at": "2099-01-01 00:00:00",
		}.items():
			with self.subTest(field=field):
				doc = frappe.get_doc("Passport Invitation", name)
				doc.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					doc.save()

	def test_generic_write_cannot_accept(self):
		name, _ = invitations.issue(self.user)
		doc = frappe.get_doc("Passport Invitation", name)
		doc.status = "Accepted"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_forged_new_invitation_rejected(self):
		name, _ = invitations.issue(self.user)
		for field, value in {
			"inviter": self.user,
			"status": "Accepted",
			"token_digest": "raw-secret",
			"expires_at": "2000-01-01 00:00:00",
		}.items():
			with self.subTest(field=field):
				doc = frappe.copy_doc(frappe.get_doc("Passport Invitation", name))
				doc.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					doc.insert(ignore_permissions=True)

	def test_cancelled_invitation_cannot_reopen(self):
		name, _ = invitations.issue(self.user)
		invitations.cancel(name)
		doc = frappe.get_doc("Passport Invitation", name)
		doc.status = "Pending"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_disabled_or_privileged_account_rejected(self):
		frappe.db.set_value("User", self.user, "enabled", 0)
		for user in (self.user, "Administrator", "Guest"):
			with self.subTest(user=user), self.assertRaises(frappe.ValidationError):
				invitations.issue(user)

	def test_issue_and_cancel_do_not_commit(self):
		frappe.db.savepoint("invitation_test")
		name, _ = invitations.issue(self.user)
		invitations.cancel(name)
		frappe.db.rollback(save_point="invitation_test")
		self.assertFalse(frappe.db.exists("Passport Invitation", name))
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": "Passport Invitation", "docname": name}))

	def queue_invitation(self):
		account = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_id": "sender@example.invalid",
				"email_account_name": "Fixture",
			}
		)
		with (
			patch.dict(frappe.conf, {"host_name": "https://test.local"}),
			patch(
				"frappe.email.doctype.email_account.email_account.EmailAccount.find_outgoing",
				return_value=account,
			),
			patch(
				"frappe.email.doctype.email_queue.email_queue.EmailQueue.send",
				side_effect=AssertionError("Unexpected SMTP"),
			),
		):
			return invitations.issue_and_queue(self.user)

	def test_delivery_uses_native_queue_and_deduplicates_pending_invitation(self):
		name = self.queue_invitation()
		self.assertEqual(self.queue_invitation(), name)
		queues = frappe.get_all(
			"Email Queue",
			filters={"reference_doctype": "Passport Invitation", "reference_name": name},
			pluck="name",
		)
		self.assertEqual(len(queues), 1)
		queue = frappe.get_doc("Email Queue", queues[0])
		self.assertEqual(queue.status, "Not Sent")
		self.assertEqual([row.recipient for row in queue.recipients], [self.user])
		mail = message_from_string(queue.message)
		text = "\n".join(
			part.get_payload(decode=True).decode()
			for part in mail.walk()
			if part.get_content_type() == "text/html"
		)
		link = unescape(re.search(r"https://test.local/passport/invitation\?[^\s<>\"]+", text).group())
		query = parse_qs(urlparse(link).query)
		self.assertEqual(query["name"], [name])
		self.assertEqual(
			protocol.digest(query["secret"][0].encode()),
			frappe.db.get_value("Passport Invitation", name, "token_digest"),
		)
		self.assertNotIn(query["secret"][0], frappe.get_doc("Passport Invitation", name).as_json())

	def test_delivery_rolls_back_both_records_with_caller(self):
		frappe.db.savepoint("delivery_caller")
		name = self.queue_invitation()
		frappe.db.rollback(save_point="delivery_caller")
		self.assertFalse(frappe.db.exists("Passport Invitation", name))
		self.assertFalse(
			frappe.db.exists(
				"Email Queue", {"reference_doctype": "Passport Invitation", "reference_name": name}
			)
		)

	def test_concurrent_delivery_reuses_committed_invitation(self):
		frappe.db.commit()
		name = self.queue_invitation()
		started, finished = threading.Event(), threading.Event()
		results, errors = [], []
		sites = frappe.local.sites_path

		def submit():
			frappe.init(site="test.local", sites_path=sites)
			frappe.connect()
			frappe.in_test = True
			try:
				frappe.set_user("Administrator")
				frappe.db.count("Passport Invitation")
				started.set()
				results.append(self.queue_invitation())
				frappe.db.commit()
			except Exception as error:
				errors.append(error)
			finally:
				frappe.db.rollback()
				frappe.destroy()
				finished.set()

		worker = threading.Thread(target=submit)
		worker.start()
		try:
			self.assertTrue(started.wait(5))
			self.assertFalse(finished.wait(0.2))
			frappe.db.commit()
		finally:
			frappe.db.rollback()
			worker.join(10)
		self.assertFalse(worker.is_alive())
		self.assertFalse(errors)
		self.assertEqual(results, [name])
		queues = frappe.get_all(
			"Email Queue",
			filters={"reference_doctype": "Passport Invitation", "reference_name": name},
			pluck="name",
		)
		self.assertEqual(len(queues), 1)
		for queue in queues:
			frappe.delete_doc("Email Queue", queue, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	def test_delivery_failure_leaves_no_invitation_even_when_caught(self):
		for outcome in (None, frappe.ValidationError("Missing outgoing account")):
			with self.subTest(outcome=type(outcome).__name__):
				with (
					patch.dict(frappe.conf, {"host_name": "https://test.local"}),
					patch.object(
						frappe,
						"sendmail",
						side_effect=outcome if isinstance(outcome, Exception) else None,
						return_value=None,
					),
				):
					with self.assertRaises(frappe.ValidationError):
						invitations.issue_and_queue(self.user)
				self.assertFalse(frappe.db.exists("Passport Invitation", {"user": self.user}))

	def test_delivery_requires_manager_and_configured_origin(self):
		frappe.set_user(self.user)
		with self.assertRaises(frappe.PermissionError):
			invitations.issue_and_queue(self.user)
		frappe.set_user("Administrator")
		for origin in (
			"",
			"javascript:alert(1)",
			"https://test.local/path",
			"https://user@test.local",
			"https://test.local?redirect=evil",
		):
			with self.subTest(origin=origin), patch.dict(frappe.conf, {"host_name": origin}):
				with self.assertRaises(frappe.ValidationError):
					invitations.issue_and_queue(self.user)
		self.assertFalse(frappe.db.exists("Passport Invitation", {"user": self.user}))

	def test_admin_send_checks_linking_and_admission_configuration(self):
		settings = {"passport_allow_linking": True, "passport_disabled": False}
		store.put("admission_policy", "site", {"mode": "enforced"})
		with (
			patch.object(invitations, "_require_form"),
			patch.object(
				auth, "config", return_value={"issuer": "https://issuer.example.invalid"}
			) as configured,
			patch.object(frappe, "get_system_settings", return_value=0) as system_settings,
			patch.object(invitations, "issue_and_queue", return_value="fixture-invitation") as queue,
			patch.dict(frappe.conf, settings),
		):
			for changes in ({"passport_allow_linking": False}, {"passport_disabled": True}):
				with patch.dict(frappe.conf, changes), self.assertRaises(frappe.ValidationError):
					invitations.send_invitation(self.user)
			configured.return_value = None
			with self.assertRaises(frappe.ValidationError):
				invitations.send_invitation(self.user)
			configured.return_value = {"issuer": "https://issuer.example.invalid"}
			for setting in ("enable_two_factor_auth", "disable_user_pass_login"):
				system_settings.side_effect = lambda key: int(key == setting)
				with self.assertRaises(frappe.ValidationError):
					invitations.send_invitation(self.user)
			system_settings.side_effect = None
			for mode in ("compatibility", "unknown"):
				store.put("admission_policy", "site", {"mode": mode})
				with self.assertRaises(frappe.ValidationError):
					invitations.send_invitation(self.user)
			frappe.db.sql("DELETE FROM passport_state WHERE kind='admission_policy' AND id='site'")
			with self.assertRaises(frappe.ValidationError):
				invitations.send_invitation(self.user)
			queue.assert_not_called()
			store.put("admission_policy", "site", {"mode": "enforced"})
			self.assertEqual(invitations.send_invitation(self.user), {"name": "fixture-invitation"})
			queue.assert_called_once_with(self.user)

	def test_cancelled_invitation_can_be_reissued_without_reusing_secret(self):
		first = self.queue_invitation()
		invitations.cancel(first)
		second = self.queue_invitation()
		self.assertNotEqual(first, second)
		self.assertNotEqual(
			frappe.db.get_value("Passport Invitation", first, "token_digest"),
			frappe.db.get_value("Passport Invitation", second, "token_digest"),
		)


if __name__ == "__main__":
	sites = Path("/tmp/passport-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	frappe.init(site="test.local", sites_path=str(sites))
	frappe.connect()
	frappe.in_test = True
	try:
		frappe.set_user("Administrator")
		frappe.reload_doc("passport_client", "doctype", "passport_invitation", force=True)
		frappe.db.commit()
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(InvitationTests)
		)
		if not result.wasSuccessful():
			raise SystemExit(1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
