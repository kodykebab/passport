"""Exercise admission binding and audit rules using real Frappe documents."""

import json
import threading
import unittest
import uuid
from pathlib import Path

import frappe
from passport_client import admission, store


class AdmissionIdentityTests(unittest.TestCase):
	def setUp(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		self.user = f"passport-admission-{uuid.uuid4().hex}@example.invalid"
		self.addCleanup(self.cleanup_fixture)
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.user,
				"first_name": "Admission Fixture",
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
		frappe.set_user("Administrator")
		for name in frappe.get_all("Passport Identity", filters={"user": self.user}, pluck="name"):
			frappe.delete_doc("Passport Identity", name, ignore_permissions=True, delete_permanently=True)
		frappe.db.sql("DELETE FROM passport_state WHERE user=%s", self.user)
		if frappe.db.exists("User", self.user):
			frappe.delete_doc("User", self.user, ignore_permissions=True, delete_permanently=True)
		frappe.db.commit()

	def test_link_starts_unapproved_and_noop_preserves_epoch(self):
		self.assertEqual(self.identity.admitted, 0)
		epoch = self.identity.access_epoch
		self.identity.save()
		self.assertEqual(self.identity.access_epoch, epoch)

	def test_grant_revoke_readmit_rotate_epoch_without_changing_user(self):
		epochs = {self.identity.access_epoch}
		before = frappe.get_doc("User", self.user).as_dict()
		for approval in (1, 0, 1):
			self.identity.admitted = approval
			self.identity.save()
			self.assertNotIn(self.identity.access_epoch, epochs)
			epochs.add(self.identity.access_epoch)
		self.assertEqual(frappe.get_doc("User", self.user).as_dict(), before)

	def test_binding_change_clears_approval_even_if_caller_retains_it(self):
		self.identity.admitted = 1
		self.identity.save()
		epoch = self.identity.access_epoch
		self.identity.subject = uuid.uuid4().hex
		self.identity.save()
		self.assertEqual(self.identity.admitted, 0)
		self.assertNotEqual(self.identity.access_epoch, epoch)

	def test_self_service_cannot_grant_even_with_ignore_permissions(self):
		frappe.set_user(self.user)
		self.identity.admitted = 1
		with self.assertRaises(frappe.PermissionError):
			self.identity.save(ignore_permissions=True)
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Identity", self.identity.name, "admitted"), 0)

	def test_self_service_cannot_insert_preapproved_link(self):
		frappe.set_user(self.user)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Passport Identity",
					"issuer": "https://other.example.invalid",
					"subject": uuid.uuid4().hex,
					"user": self.user,
					"admitted": 1,
				}
			).insert(ignore_permissions=True)

	def test_native_version_records_approval_and_rolls_back_with_it(self):
		self.identity.admitted = 1
		# Frappe suppresses Version records by default while in_test is set.
		self.identity.save(ignore_version=False)
		versions = frappe.get_all(
			"Version",
			filters={
				"ref_doctype": "Passport Identity",
				"docname": self.identity.name,
			},
			fields=["data", "owner"],
		)
		self.assertTrue(
			any(
				["admitted", 0, 1] in json.loads(row.data).get("changed", []) and row.owner == "Administrator"
				for row in versions
			)
		)
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Identity", self.identity.name, "admitted"), 0)
		self.assertFalse(
			frappe.db.exists(
				"Version",
				{
					"ref_doctype": "Passport Identity",
					"docname": self.identity.name,
				},
			)
		)

	def test_stale_document_cannot_overwrite_revocation(self):
		self.identity.admitted = 1
		self.identity.save()
		frappe.db.commit()
		stale = frappe.get_doc("Passport Identity", self.identity.name)
		self.identity.admitted = 0
		self.identity.save()
		frappe.db.commit()
		with self.assertRaises(frappe.TimestampMismatchError):
			stale.save()
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Passport Identity", self.identity.name, "admitted"), 0)

	def association(self):
		self.identity.reload()
		return {
			"association_id": self.identity.name,
			"access_epoch": self.identity.access_epoch,
			"issuer": self.identity.issuer,
			"sub": self.identity.subject,
			"user": self.user,
		}

	def test_enforcement_and_missing_policy_deny_unapproved_identity(self):
		row = self.association()
		for policy in ({"mode": "enforced"}, {"mode": "unknown"}, {}):
			store.put(admission.POLICY_KIND, admission.POLICY_ID, policy)
			self.assertFalse(admission.permits(row, lock=True))
		frappe.db.sql(
			"DELETE FROM passport_state WHERE kind=%s AND id=%s", (admission.POLICY_KIND, admission.POLICY_ID)
		)
		self.assertFalse(admission.permits(row, lock=True))
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		self.assertTrue(admission.permits(row, lock=True))

	def test_activation_approves_reviewed_identity_and_rejects_old_epoch(self):
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		old = self.association()
		reviewed = admission.preview()
		admission.activate(reviewed, [self.identity.name])
		self.assertTrue(admission.permits(self.association(), lock=True))
		self.assertFalse(admission.permits(old, lock=True))
		policy = store.get(admission.POLICY_KIND, admission.POLICY_ID)
		self.assertEqual(policy["activated_by"], "Administrator")
		epoch = self.identity.access_epoch
		admission.activate(reviewed, [self.identity.name])
		self.assertEqual(self.association()["access_epoch"], epoch)

	def test_activation_rolls_back_approval_policy_and_audit(self):
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		frappe.db.commit()
		before = self.association()
		admission.activate(admission.preview(), [self.identity.name])
		frappe.db.rollback()
		self.assertEqual(self.association(), before)
		self.assertEqual(store.get(admission.POLICY_KIND, admission.POLICY_ID)["mode"], "compatibility")
		self.assertFalse(
			frappe.db.exists("Version", {"ref_doctype": "Passport Identity", "docname": self.identity.name})
		)

	def test_activation_rejects_stale_review_and_nonadministrator(self):
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		reviewed = admission.preview()
		self.identity.subject = uuid.uuid4().hex
		self.identity.save()
		with self.assertRaises(frappe.ValidationError):
			admission.activate(reviewed, [self.identity.name])
		frappe.set_user(self.user)
		with self.assertRaises(frappe.PermissionError):
			admission.activate({}, [])

	def test_initializers_preserve_existing_policy_and_fresh_install_enforces(self):
		frappe.db.sql(
			"DELETE FROM passport_state WHERE kind=%s AND id=%s", (admission.POLICY_KIND, admission.POLICY_ID)
		)
		admission.after_install()
		self.assertEqual(store.get(admission.POLICY_KIND, admission.POLICY_ID)["mode"], "enforced")
		admission.initialize_existing()
		self.assertEqual(store.get(admission.POLICY_KIND, admission.POLICY_ID)["mode"], "enforced")

	def test_upgrade_initializes_compatibility_but_cannot_recreate_deleted_policy(self):
		already_migrated = frappe.db.exists("Patch Log", {"patch": admission.MIGRATION, "skipped": 0})
		frappe.db.sql(
			"DELETE FROM passport_state WHERE kind=%s AND id=%s", (admission.POLICY_KIND, admission.POLICY_ID)
		)
		if already_migrated:
			with self.assertRaises(frappe.ValidationError):
				admission.initialize_existing()
			return
		admission.initialize_existing()
		self.assertEqual(store.get(admission.POLICY_KIND, admission.POLICY_ID)["mode"], "compatibility")
		log = frappe.get_doc({"doctype": "Patch Log", "patch": admission.MIGRATION, "skipped": 0})
		log.insert(ignore_permissions=True)
		try:
			frappe.db.sql(
				"DELETE FROM passport_state WHERE kind=%s AND id=%s",
				(admission.POLICY_KIND, admission.POLICY_ID),
			)
			with self.assertRaises(frappe.ValidationError):
				admission.initialize_existing()
		finally:
			frappe.db.delete("Patch Log", {"name": log.name})
			frappe.db.commit()

	def test_activation_waits_for_actual_locked_authority_query(self):
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		frappe.db.commit()
		row, reviewed = self.association(), admission.preview()
		ready, release = threading.Event(), threading.Event()
		outcome = []

		def reader():
			frappe.init(site="test.local", sites_path="/tmp/passport-acceptance-20261029/sites")
			frappe.connect()
			try:
				outcome.append(admission.permits(row, lock=True))
				ready.set()
				if not release.wait(10):
					raise TimeoutError("Authority reader was not released")
			except Exception as error:
				outcome.append(error)
				ready.set()
			finally:
				frappe.db.rollback()
				frappe.destroy()

		worker = threading.Thread(target=reader)
		worker.start()
		previous_timeout = frappe.db.sql("SELECT @@innodb_lock_wait_timeout")[0][0]
		try:
			self.assertTrue(ready.wait(10))
			self.assertEqual(outcome, [True])
			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=1")
			with self.assertRaises(frappe.QueryTimeoutError):
				admission.activate(reviewed, [self.identity.name])
			frappe.db.rollback()
		finally:
			release.set()
			worker.join(timeout=10)
			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=%s", previous_timeout)
		self.assertFalse(worker.is_alive())
		self.assertEqual(outcome, [True])
		admission.activate(reviewed, [self.identity.name])
		self.assertFalse(admission.permits(row, lock=True))

	def test_concurrent_document_edit_invalidates_review_after_commit(self):
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		frappe.db.commit()
		reviewed = admission.preview()
		ready, release = threading.Event(), threading.Event()
		outcome = []
		name = self.identity.name

		def editor():
			frappe.init(site="test.local", sites_path="/tmp/passport-acceptance-20261029/sites")
			frappe.connect()
			frappe.set_user("Administrator")
			try:
				document = frappe.get_doc("Passport Identity", name)
				document.subject = uuid.uuid4().hex
				document.save()
				ready.set()
				if not release.wait(10):
					raise TimeoutError("Document editor was not released")
				frappe.db.commit()
				outcome.append("committed")
			except Exception as error:
				outcome.append(error)
				ready.set()
			finally:
				frappe.db.rollback()
				frappe.destroy()

		worker = threading.Thread(target=editor)
		worker.start()
		previous_timeout = frappe.db.sql("SELECT @@innodb_lock_wait_timeout")[0][0]
		try:
			self.assertTrue(ready.wait(10))
			self.assertEqual(outcome, [])
			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=1")
			with self.assertRaises(frappe.QueryTimeoutError):
				admission.activate(reviewed, [name])
			frappe.db.rollback()
		finally:
			release.set()
			worker.join(timeout=10)
			frappe.db.sql("SET SESSION innodb_lock_wait_timeout=%s", previous_timeout)
		self.assertFalse(worker.is_alive())
		self.assertEqual(outcome, ["committed"])
		with self.assertRaises(frappe.ValidationError):
			admission.activate(reviewed, [name])
		self.assertEqual(store.get(admission.POLICY_KIND, admission.POLICY_ID)["mode"], "compatibility")

	def test_new_association_waits_for_activation_and_starts_denied(self):
		store.put(admission.POLICY_KIND, admission.POLICY_ID, {"mode": "compatibility"})
		frappe.db.commit()
		admission.activate(admission.preview(), [self.identity.name])
		values = {
			"doctype": "Passport Identity",
			"issuer": "https://concurrent.example.invalid",
			"subject": uuid.uuid4().hex,
			"user": self.user,
		}
		outcome = []

		def inserter():
			frappe.init(site="test.local", sites_path="/tmp/passport-acceptance-20261029/sites")
			frappe.connect()
			frappe.set_user("Administrator")
			try:
				frappe.db.sql("SET SESSION innodb_lock_wait_timeout=1")
				frappe.get_doc(values).insert()
				outcome.append("unexpected insert")
			except frappe.QueryTimeoutError:
				outcome.append("blocked")
			except Exception as error:
				outcome.append(error)
			finally:
				frappe.db.rollback()
				frappe.destroy()

		worker = threading.Thread(target=inserter)
		worker.start()
		worker.join(timeout=10)
		self.assertFalse(worker.is_alive())
		self.assertEqual(outcome, ["blocked"])
		frappe.db.commit()
		created = frappe.get_doc(values).insert()
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


if __name__ == "__main__":
	sites = Path("/tmp/passport-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	frappe.init(site="test.local", sites_path=str(sites))
	frappe.connect()
	frappe.in_test = True
	previous_policy = store.get(admission.POLICY_KIND, admission.POLICY_ID)
	try:
		frappe.set_user("Administrator")
		frappe.reload_doc("passport_client", "doctype", "passport_identity", force=True)
		frappe.db.commit()
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(AdmissionIdentityTests)
		)
		if not result.wasSuccessful():
			raise SystemExit(1)
	finally:
		frappe.db.rollback()
		if previous_policy is None:
			frappe.db.sql(
				"DELETE FROM passport_state WHERE kind=%s AND id=%s",
				(admission.POLICY_KIND, admission.POLICY_ID),
			)
		else:
			store.put(admission.POLICY_KIND, admission.POLICY_ID, previous_policy)
		frappe.db.commit()
		frappe.destroy()
