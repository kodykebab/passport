import re
import socket
import unittest
from email import message_from_bytes
from html import unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe
from aiosmtpd.controller import Controller
from frappe.email import queue as email_queue
from invitation_integration import InvitationTests
from passport_client import invitations, protocol


class MailSink:
	def __init__(self):
		self.messages = []
		self.failures = 0
		self.attempts = 0

	async def handle_DATA(self, server, session, envelope):
		self.attempts += 1
		if self.failures:
			self.failures -= 1
			return "451 Temporary fixture failure"
		self.messages.append((envelope.rcpt_tos, envelope.content))
		return "250 Message accepted by local fixture"


class InvitationSMTPTests(InvitationTests):
	def setUp(self):
		super().setUp()
		self.sink = MailSink()
		with socket.socket() as listener:
			listener.bind(("127.0.0.1", 0))
			port = listener.getsockname()[1]
		self.controller = Controller(self.sink, hostname="127.0.0.1", port=port)
		self.controller.start()
		self.addCleanup(self.controller.stop)
		self.account = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_id": "sender@example.invalid",
				"email_account_name": "Local SMTP Fixture",
				"smtp_server": "127.0.0.1",
				"smtp_port": port,
				"no_smtp_authentication": 1,
				"use_tls": 0,
				"use_ssl_for_outgoing": 0,
			}
		)
		self.addCleanup(lambda: self.account.get_smtp_server().quit())
		for replacement in (
			patch("frappe.utils.sentry.capture_exception"),
			patch.dict(frappe.conf, {"host_name": "https://test.local", "mute_emails": 0}),
			patch.dict(frappe.flags, {"testing_email": True, "mute_emails": False}),
			patch(
				"frappe.email.doctype.email_account.email_account.EmailAccount.find_outgoing",
				return_value=self.account,
			),
		):
			replacement.start()
			self.addCleanup(replacement.stop)
		self.assertNotEqual(str(frappe.db.get_default("suspend_email_queue")), "1")

	def create_delivery(self):
		name = invitations.issue_and_queue(self.user)
		queue = frappe.db.get_value(
			"Email Queue", {"reference_doctype": "Passport Invitation", "reference_name": name}
		)
		frappe.db.sql(
			"UPDATE `tabEmail Queue` SET creation=DATE_SUB(NOW(), INTERVAL 20 SECOND) WHERE name=%s", queue
		)
		frappe.db.commit()
		return name, queue

	def flush_delivery(self, queue):
		rows = [row for row in email_queue.get_queue() if row.name == queue]
		self.assertEqual(len(rows), 1)
		with patch.object(email_queue, "get_queue", return_value=rows):
			email_queue.flush()
		frappe.db.rollback()
		return frappe.get_doc("Email Queue", queue)

	def delivered_secret(self, name):
		self.assertEqual(len(self.sink.messages), 1)
		recipients, content = self.sink.messages[0]
		self.assertEqual(recipients, [self.user])
		mail = message_from_bytes(content)
		html = "\n".join(
			part.get_payload(decode=True).decode()
			for part in mail.walk()
			if part.get_content_type() == "text/html"
		)
		link = unescape(re.search(r"https://test.local/passport/invitation\?[^\s<>\"]+", html).group())
		query = parse_qs(urlparse(link).query)
		self.assertEqual(query["name"], [name])
		self.assertEqual(
			protocol.digest(query["secret"][0].encode()),
			frappe.db.get_value("Passport Invitation", name, "token_digest"),
		)
		return query["secret"][0]

	def test_smtp_scheduler_delivery_and_sent_message_not_repeated(self):
		name, queue = self.create_delivery()
		document = self.flush_delivery(queue)
		self.assertEqual(document.status, "Sent")
		self.assertTrue(all(row.status == "Sent" for row in document.recipients))
		self.delivered_secret(name)
		document.send()
		self.assertEqual(self.sink.attempts, 1)
		self.assertNotIn(queue, [row.name for row in email_queue.get_queue()])

	def test_smtp_temporary_failure_retries_same_invitation(self):
		self.sink.failures = 1
		name, queue = self.create_delivery()
		document = self.flush_delivery(queue)
		self.assertEqual(document.status, "Not Sent")
		self.assertEqual(document.retry, 1)
		self.assertEqual(len(self.sink.messages), 0)
		self.assertEqual(invitations.issue_and_queue(self.user), name)
		frappe.db.commit()
		document = self.flush_delivery(queue)
		self.assertEqual(document.status, "Sent")
		self.assertEqual(self.sink.attempts, 2)
		self.delivered_secret(name)
		self.assertEqual(
			frappe.db.count(
				"Email Queue", {"reference_doctype": "Passport Invitation", "reference_name": name}
			),
			1,
		)

	def test_smtp_cancelled_invitation_email_cannot_authorize_acceptance(self):
		name, queue = self.create_delivery()
		invitations.cancel(name)
		frappe.db.commit()
		self.assertEqual(self.flush_delivery(queue).status, "Sent")
		secret = self.delivered_secret(name)
		with self.assertRaises(frappe.PermissionError):
			invitations._check_pending(
				frappe.get_doc("Passport Invitation", name), self.user, protocol.digest(secret.encode())
			)

	def test_smtp_exhausted_retries_leave_operator_visible_error(self):
		from frappe.email.doctype.email_queue.email_queue import get_email_retry_limit

		name, queue = self.create_delivery()
		limit = get_email_retry_limit()
		self.sink.failures = limit + 1
		for _ in range(limit + 1):
			document = self.flush_delivery(queue)
		self.assertEqual(document.status, "Error")
		self.assertEqual(document.retry, limit)
		self.assertIn("451", document.error)
		self.assertEqual(len(self.sink.messages), 0)
		self.assertNotIn(queue, [row.name for row in email_queue.get_queue()])
		self.assertEqual(frappe.db.get_value("Passport Invitation", name, "status"), "Pending")
		self.assertTrue(
			frappe.db.exists("Notification Log", {"document_type": "Email Queue", "document_name": queue})
		)

	def test_smtp_stale_sending_entry_recovers_through_native_scheduler(self):
		name, queue = self.create_delivery()
		frappe.db.sql(
			"UPDATE `tabEmail Queue` SET status='Sending',modified=DATE_SUB(NOW(), INTERVAL 16 MINUTE) WHERE name=%s",
			queue,
		)
		frappe.db.commit()
		original = frappe.get_all

		def only_this_queue(doctype, **kwargs):
			if doctype == "Email Queue":
				kwargs["filters"] = {**kwargs.get("filters", {}), "name": queue}
			return original(doctype, **kwargs)

		with patch.object(frappe, "get_all", side_effect=only_this_queue):
			email_queue.retry_sending_emails()
		document = frappe.get_doc("Email Queue", queue)
		self.assertEqual(document.status, "Not Sent")
		self.assertEqual(document.retry, 1)
		self.assertEqual(self.flush_delivery(queue).status, "Sent")
		self.delivered_secret(name)


if __name__ == "__main__":
	sites = Path("/tmp/passport-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	frappe.init(site="test.local", sites_path=str(sites))
	frappe.connect()
	frappe.in_test = True
	try:
		tests = [
			InvitationSMTPTests(name)
			for name in unittest.defaultTestLoader.getTestCaseNames(InvitationSMTPTests)
			if name.startswith("test_smtp_")
		]
		result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
		if not result.wasSuccessful():
			raise SystemExit(1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
