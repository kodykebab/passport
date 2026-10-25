"""Self-service deletion authorization and durable failure boundaries."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from passport import account, deletion


class DeletionTests(unittest.TestCase):
	def setUp(self):
		frappe.local.db = Mock()
		self.addCleanup(setattr, frappe.local, "db", None)

	def test_standard_and_operator_accounts_are_ineligible(self):
		with patch.object(frappe, "get_roles", return_value=["System Manager"]):
			for user in ("Guest", "Administrator", "operator@example.test"):
				self.assertFalse(deletion.eligible(user))
				with self.assertRaises(ValueError):
					deletion.request(user)
		frappe.db.commit.assert_not_called()

	def test_disable_and_generation_are_committed_before_native_cleanup(self):
		events = []
		frappe.db.get_value.side_effect = ["subject", "created"]
		frappe.db.set_value.side_effect = lambda *args: events.append("disable")
		frappe.db.commit.side_effect = lambda: events.append("commit")
		with (
			patch.object(deletion, "eligible", return_value=True),
			patch.object(deletion.store, "put", side_effect=lambda *args: events.append("intent")),
			patch.object(deletion.store, "revoke_user", side_effect=lambda user: events.append("generation")),
			patch.object(deletion, "finish", side_effect=lambda subject: events.append("finish") or False),
		):
			self.assertFalse(deletion.request("user"))
		self.assertEqual(events, ["intent", "disable", "generation", "commit", "finish"])

	def test_pending_deletion_blocks_native_session_creation(self):
		with patch.object(account.deletion, "pending", return_value=True):
			with self.assertRaises(frappe.AuthenticationError):
				account.insert(SimpleNamespace(user="user"))

	def test_native_failure_preserves_pending_intent(self):
		row = {"user": "user", "created": "created"}
		frappe.db.exists.return_value = True
		frappe.db.get_value.return_value = "created"
		frappe.db.sql.return_value = []
		with (
			patch.object(deletion.store, "get", return_value=row),
			patch.object(frappe, "delete_doc", side_effect=RuntimeError("linked")),
			patch.object(frappe, "log_error"),
			patch.object(deletion.store, "put") as put,
		):
			self.assertFalse(deletion.finish("subject"))
			put.assert_not_called()
		frappe.db.rollback.assert_called_once()

	def test_retry_never_deletes_a_replacement_user(self):
		frappe.db.exists.return_value = True
		frappe.db.get_value.return_value = "new creation"
		with (
			patch.object(deletion.store, "get", return_value={"user": "user", "created": "old creation"}),
			patch.object(frappe, "delete_doc") as delete,
			patch.object(frappe, "log_error"),
		):
			self.assertFalse(deletion.finish("subject"))
			delete.assert_not_called()
