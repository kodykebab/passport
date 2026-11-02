import argparse
import copy
import json
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.sessions import Session, delete_session
from passport_client import admission, federation, linking, store
from passport_core.store import native_session_id
from werkzeug.wrappers import Request


class SessionAuthorityTests(unittest.TestCase):
	def test_installation_change_rejects_old_federated_session_after_reenable(self):
		with patch.dict(frappe.conf, {"passport_installation": None, "passport_disabled": False}):
			native = self.create_session(federated=False)
			session = self.create_session()
			row = store.get("federation", native_session_id(session.sid))
			self.assertTrue(federation.get_valid_local_session(session.sid, row))
			frappe.conf.passport_installation = uuid.uuid4().hex
			frappe.conf.passport_disabled = True
			self.assertIsNone(federation.get_valid_local_session(session.sid, row))
			frappe.conf.passport_disabled = False
			self.assertIsNone(federation.get_valid_local_session(session.sid, row))
			with self.assertRaises(frappe.AuthenticationError):
				self.create_session()
			self.assertTrue(store.get_valid_native_session(native.sid, self.user))
			policy = store.get("admission_policy", "site")
			policy["installation"] = frappe.conf.passport_installation
			store.put("admission_policy", "site", policy)
			fresh = self.create_session(installation=frappe.conf.passport_installation)
			self.assertTrue(
				federation.get_valid_local_session(
					fresh.sid, store.get("federation", native_session_id(fresh.sid))
				)
			)

	def setUp(self):
		frappe.db.rollback()
		self.enterContext(
			patch.dict(frappe.conf, {"passport_installation": None, "passport_disabled": False})
		)
		self.user = f"passport-acceptance-{uuid.uuid4().hex}@example.invalid"
		self.sessions = []
		self.addCleanup(self.cleanup_fixture)
		frappe.set_user("Administrator")
		store.put("admission_policy", "site", {"mode": "compatibility"})
		frappe.local.request = Request.from_values(path="/passport/status", base_url="http://test.local")
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.form_dict = frappe._dict()
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.user,
				"first_name": "Passport Acceptance",
				"enabled": 1,
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self.identity = frappe.get_doc(
			{
				"doctype": "Passport Identity",
				"issuer": "https://issuer.example.invalid",
				"subject": uuid.uuid4().hex,
				"user": self.user,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def cleanup_fixture(self):
		frappe.db.rollback()
		frappe.flags.pop("passport_pending", None)
		frappe.flags.pop("passport_background", None)
		frappe.set_user("Administrator")
		for sid in self.sessions:
			delete_session(sid, user=self.user, reason="Disposable Passport acceptance fixture")
			frappe.cache.hdel("session", native_session_id(sid))
		for name in frappe.get_all("Passport Identity", filters={"user": self.user}, pluck="name"):
			frappe.delete_doc("Passport Identity", name, ignore_permissions=True, delete_permanently=True)
		frappe.db.sql("DELETE FROM passport_state WHERE user=%s", self.user)
		if frappe.db.exists("User", self.user):
			frappe.delete_doc("User", self.user, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	def create_session(self, federated=True, installation=None):
		deadline = time.time() + 600
		if federated:
			frappe.flags.passport_pending = {
				"installation": installation,
				"association_id": self.identity.name,
				"access_epoch": self.identity.access_epoch,
				"protocol_sid": uuid.uuid4().hex,
				"binding": uuid.uuid4().hex,
				"user": self.user,
				"sub": self.identity.subject,
				"issuer": self.identity.issuer,
				"deadline": deadline,
				"exp": time.time() + 300,
				"revoked": False,
				"revision": 0,
			}
		else:
			frappe.flags.pop("passport_pending", None)
		try:
			session = Session(
				user=self.user,
				full_name="Passport Acceptance",
				user_type="Website User",
				session_end=datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
			)
		finally:
			frappe.flags.pop("passport_pending", None)
		self.sessions.append(session.sid)
		frappe.local.session_obj = session
		frappe.local.session = session.data
		return session

	def test_native_insert_binds_federation_and_generation(self):
		session = self.create_session()
		row = store.get("federation", native_session_id(session.sid))
		native = store.get_valid_native_session(session.sid, self.user)
		self.assertTrue(native["passport"])
		self.assertEqual(native["passport_generation"], store.generation(self.user))
		self.assertEqual(row["sub"], self.identity.subject)
		self.assertIsNotNone(federation.get_valid_local_session(session.sid, row))

	def test_enforced_policy_blocks_new_unapproved_session(self):
		store.put("admission_policy", "site", {"mode": "enforced"})
		with self.assertRaises(frappe.AuthenticationError):
			self.create_session()
		frappe.db.rollback()
		frappe.set_user("Administrator")
		store.put("admission_policy", "site", {"mode": "enforced"})
		self.identity.admitted = 1
		self.identity.save()
		session = self.create_session()
		self.assertIsNotNone(
			federation.get_valid_local_session(
				session.sid, store.get("federation", native_session_id(session.sid))
			)
		)

	def test_link_completion_waits_for_activation_and_does_not_grant_access(self):
		native = self.create_session(federated=False)
		attempt = {
			"user": self.user,
			"local_sid": native_session_id(native.sid),
			"generation": store.generation(self.user),
			"exp": time.time() + 600,
		}
		claims = {"sub": uuid.uuid4().hex}
		# Completion consumes already-verified proof; this test does not sign a token.
		settings = {
			"passport_allow_linking": True,
			"passport_disabled": False,
			"passport_installation": None,
			"passport_issuer": "https://link-race.example.invalid",
			"passport_client_id": "fixture",
			"passport_private_key": "unused-by-link-completion",
			"host_name": "https://test.local",
		}
		frappe.db.commit()
		frappe.set_user("Administrator")
		admission.activate(admission.preview(), [self.identity.name])
		outcome = []
		sites_path = frappe.local.sites_path

		def linker():
			frappe.init(site="test.local", sites_path=sites_path)
			frappe.connect()
			frappe.set_user(self.user)
			frappe.local.session = frappe._dict(user=self.user, sid=native.sid)
			frappe.conf.update(settings)
			try:
				frappe.db.sql("SET SESSION innodb_lock_wait_timeout=1")
				linking.finish(attempt, claims)
				outcome.append("unexpected completion")
			except frappe.QueryTimeoutError:
				outcome.append("blocked")
			except Exception as error:
				outcome.append(error)
			finally:
				frappe.db.rollback()
				frappe.destroy()

		worker = threading.Thread(target=linker)
		worker.start()
		worker.join(timeout=10)
		self.assertFalse(worker.is_alive())
		self.assertEqual(outcome, ["blocked"])
		frappe.db.commit()
		frappe.set_user(self.user)
		frappe.local.session = frappe._dict(user=self.user, sid=native.sid)
		with patch.dict(frappe.conf, settings):
			self.assertEqual(linking.finish(attempt, claims).status_code, 303)
		created = frappe.get_doc(
			"Passport Identity", {"issuer": settings["passport_issuer"], "subject": claims["sub"]}
		)
		self.assertEqual(created.admitted, 0)
		self.assertFalse(
			admission.permits(
				{
					"association_id": created.name,
					"access_epoch": created.access_epoch,
					"issuer": created.issuer,
					"sub": created.subject,
					"user": created.user,
				},
				lock=True,
			)
		)
		self.assertIsNotNone(store.get_valid_native_session(native.sid, self.user))

	def test_admission_revocation_preserves_native_session_and_readmission_does_not_revive_old_session(self):
		self.identity.admitted = 1
		self.identity.save()
		federated = self.create_session()
		row = store.get("federation", native_session_id(federated.sid))
		native = self.create_session(federated=False)
		frappe.db.commit()
		for admitted in (0, 1):
			frappe.set_user("Administrator")
			self.identity.admitted = admitted
			self.identity.save()
			frappe.db.commit()
			self.assertIsNone(federation.get_valid_local_session(federated.sid, row, lock=True))
			self.assertIsNotNone(store.get_valid_native_session(native.sid, self.user))
		fresh = self.create_session()
		self.assertIsNotNone(
			federation.get_valid_local_session(
				fresh.sid, store.get("federation", native_session_id(fresh.sid))
			)
		)

	def test_logout_rejects_stale_cache_and_old_federation_snapshot(self):
		session = self.create_session()
		old_row = store.get("federation", native_session_id(session.sid))
		cached = copy.deepcopy(session.data)
		delete_session(session.sid, user=self.user)
		frappe.cache.hset("session", session.sid, cached)
		self.assertIsNone(federation.get_valid_local_session(session.sid, old_row, lock=True))
		self.assertFalse(federation.validate(session.sid, cached.data))
		self.assertFalse(frappe.db.exists("Sessions", {"sid": native_session_id(session.sid)}))

	def test_disable_reenable_does_not_revive_existing_session(self):
		session = self.create_session()
		row = store.get("federation", native_session_id(session.sid))
		generation = store.generation(self.user)
		frappe.set_user("Administrator")
		user = frappe.get_doc("User", self.user)
		user.enabled = 0
		user.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertGreater(store.generation(self.user), generation)
		user.enabled = 1
		user.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertIsNone(federation.get_valid_local_session(session.sid, row, lock=True))

	def test_unlink_blocks_federation_but_preserves_independent_native_session(self):
		federated = self.create_session()
		row = store.get("federation", native_session_id(federated.sid))
		native = self.create_session(federated=False)
		frappe.db.delete("Passport Identity", {"name": self.identity.name})
		frappe.db.commit()
		self.assertIsNone(federation.get_valid_local_session(federated.sid, row))
		self.assertIsNotNone(store.get_valid_native_session(native.sid, self.user))

	def test_expired_lease_has_no_socket_lifetime(self):
		session = self.create_session()
		row = store.get("federation", native_session_id(session.sid))
		row["exp"] = time.time() - 1
		store.put("federation", native_session_id(session.sid), row)
		frappe.db.commit()
		self.assertEqual(federation.socket_context(), 0)

	def test_legacy_federation_requires_fresh_login(self):
		session = self.create_session()
		row = store.get("federation", native_session_id(session.sid))
		row.pop("association_id")
		row.pop("access_epoch")
		store.put("federation", native_session_id(session.sid), row)
		frappe.db.commit()
		self.assertIsNone(federation.get_valid_local_session(session.sid, row, lock=True))

	def test_identity_change_and_reversal_do_not_restore_authority(self):
		session = self.create_session()
		row = store.get("federation", native_session_id(session.sid))
		original = self.identity.subject
		self.identity.subject = uuid.uuid4().hex
		self.identity.save(ignore_permissions=True)
		frappe.db.commit()
		self.identity.subject = original
		self.identity.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertIsNone(federation.get_valid_local_session(session.sid, row, lock=True))

	def test_migration_is_idempotent_and_preserves_identity(self):
		frappe.db.sql("UPDATE `tabPassport Identity` SET access_epoch=NULL WHERE name=%s", self.identity.name)
		frappe.db.commit()
		store.install()
		first = frappe.db.get_value("Passport Identity", self.identity.name, "access_epoch")
		store.install()
		second = frappe.db.get_value("Passport Identity", self.identity.name, "access_epoch")
		self.assertTrue(first)
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.get_value("Passport Identity", self.identity.name, "user"), self.user)

	def test_session_creation_rejects_obsolete_association_version(self):
		epoch = self.identity.access_epoch
		original = self.identity.subject
		self.identity.subject = uuid.uuid4().hex
		self.identity.save(ignore_permissions=True)
		frappe.db.commit()
		self.identity.subject = original
		self.identity.save(ignore_permissions=True)
		frappe.db.commit()
		self.identity.access_epoch = epoch
		with self.assertRaises(frappe.AuthenticationError):
			self.create_session()

	def test_migration_preserves_federation_and_stored_id_deletion_revokes_it(self):
		from passport_core.store import delete_stored_session

		session = self.create_session()
		stored_sid = native_session_id(session.sid)
		row = store.get("federation", stored_sid)
		legacy = {"user": self.user, "local_sid": session.sid, "issuer_sid": session.sid}
		store.create("fixture", stored_sid, legacy)
		if stored_sid != session.sid:
			from frappe.patches.v16_0.hash_session_ids import execute

			frappe.db.sql("UPDATE tabSessions SET sid=%s WHERE sid=%s", (session.sid, stored_sid))
			frappe.cache.hdel("session", stored_sid)
			frappe.db.sql(
				"UPDATE passport_state SET id=%s WHERE kind='federation' AND id=%s",
				(session.sid, stored_sid),
			)
			execute()
		frappe.db.commit()
		store.install()
		store.install()
		self.assertEqual(store.get("federation", stored_sid), row)
		migrated = store.get("fixture", stored_sid)
		self.assertEqual(migrated["local_sid"], stored_sid)
		self.assertEqual(migrated["issuer_sid"], stored_sid)
		self.assertIsNotNone(store.get_valid_native_session(session.sid, self.user))
		self.assertIsNotNone(store.get_valid_native_session(stored_sid, self.user, stored=True))
		delete_stored_session(stored_sid, self.user, "Disposable fixture revocation")
		self.assertIsNone(store.get_valid_native_session(session.sid, self.user))
		self.assertTrue(store.get("federation", stored_sid)["revoked"])

	def test_background_checks_do_not_extend_native_activity(self):
		session = self.create_session()
		old_activity = frappe.utils.now_datetime() - timedelta(hours=1)
		frappe.db.set_value(
			"Sessions",
			{"sid": native_session_id(session.sid)},
			"lastupdate",
			old_activity,
			update_modified=False,
		)
		frappe.db.commit()
		before = frappe.db.sql(
			"SELECT lastupdate FROM tabSessions WHERE sid=%s", native_session_id(session.sid)
		)[0][0]
		frappe.flags.passport_background = True
		session.update(force=True)
		frappe.db.commit()
		after = frappe.db.sql(
			"SELECT lastupdate FROM tabSessions WHERE sid=%s", native_session_id(session.sid)
		)[0][0]
		self.assertEqual(after, before)


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--sites-path", required=True, type=Path)
	args = parser.parse_args()
	sites_path = args.sites_path.resolve()
	marker = sites_path / "test.local" / ".passport-acceptance-fixture"
	if not marker.is_file() or marker.read_text().strip() != "disposable-passport-test-site":
		parser.error("test.local must be explicitly marked as a disposable Passport test site")
	frappe.init(site="test.local", sites_path=str(sites_path))
	frappe.connect()
	previous_test_mode = frappe.in_test
	frappe.in_test = True
	previous_policy = store.get("admission_policy", "site")
	store.put("admission_policy", "site", {"mode": "compatibility"})
	frappe.db.commit()
	try:
		if "passport_client" not in frappe.get_installed_apps():
			raise RuntimeError("Install passport_client on the disposable test.local fixture first")
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(SessionAuthorityTests)
		)
		print(json.dumps({"site": "test.local", "tests": result.testsRun, "passed": result.wasSuccessful()}))
		if not result.wasSuccessful():
			raise SystemExit(1)
	finally:
		frappe.db.rollback()
		if previous_policy is None:
			frappe.db.sql("DELETE FROM passport_state WHERE kind='admission_policy' AND id='site'")
		else:
			store.put("admission_policy", "site", previous_policy)
		frappe.db.commit()
		frappe.in_test = previous_test_mode
		frappe.destroy()


if __name__ == "__main__":
	main()
