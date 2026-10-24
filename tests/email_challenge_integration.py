"""Challenge invariants against real SQL/Redis; no mail delivered or native sessions created."""

import secrets
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from live_support import USER, db, store
from passport import account
from passport import email_login as email


class ChallengeTests(unittest.TestCase):
	def setUp(self):
		self.attempts = []

	def tearDown(self):
		with db("id.localhost"):
			for attempt in self.attempts:
				frappe.db.sql("DELETE FROM passport_state WHERE kind='email_challenge' AND id=%s", attempt)

	def challenge(self, **changes):
		attempt, browser, code = secrets.token_hex(16), secrets.token_hex(32), "123456"
		self.attempts.append(attempt)
		with db("id.localhost"):
			row = dict(
				user=USER,
				email=USER,
				browser=browser,
				user_generation=store.generation(USER),
				deadline=int(time.time()) + 900,
				expires=int(time.time()) + 300,
				resend_after=0,
				failures=0,
				spent=False,
				generation=1,
				mail=[],
				digest=email.keyed_digest(attempt, 1, code),
				**{"return": "/account"},
			)
			row.update(changes)
			store.create("email_challenge", attempt, row)
		return attempt, browser, code

	def verify(self, args, generation="1"):
		with db("id.localhost"):
			frappe.local.login_manager = SimpleNamespace(login_as=Mock())
			return email.verify(*args, generation)

	def test_expiry_binding_and_generation(self):
		for changes in ({"expires": 0}, {"deadline": 0}, {"user_generation": -1}, {"spent": True}):
			with self.subTest(changes=changes):
				with self.assertRaises(email.ChallengeError):
					self.verify(self.challenge(**changes))
		args = self.challenge()
		with self.assertRaises(email.ChallengeError):
			self.verify((args[0], "wrong-browser", args[2]))
		self.assertEqual(self.verify(args), "/account")
		with self.assertRaises(email.ChallengeError):
			self.verify(args)

	def test_resend_replaces_code_without_resetting_budget(self):
		args = self.challenge(failures=4)
		with (
			db("id.localhost"),
			patch.object(email, "queue_code") as queue,
			patch.object(email, "limit_issuance"),
		):
			email.resend(*args[:2])
			new_code = queue.call_args.args[2]
			self.assertNotEqual(new_code, args[2])
			row = store.get("email_challenge", args[0])
			self.assertEqual(row["failures"], 4)
			self.assertEqual(row["generation"], 2)
			with self.assertRaisesRegex(email.ChallengeError, "wait"):
				email.resend(*args[:2])
		with self.assertRaisesRegex(email.ChallengeError, "newer code"):
			self.verify(args)
		with self.assertRaisesRegex(email.ChallengeError, "Incorrect"):
			self.verify(args, "2")
		with self.assertRaisesRegex(email.ChallengeError, "ended"):
			self.verify((*args[:2], new_code), "2")

	def test_independent_attempts_and_concurrent_single_use(self):
		first, second = self.challenge(), self.challenge()
		barrier = threading.Barrier(2)

		def consume():
			barrier.wait(timeout=10)
			try:
				return self.verify(first)
			except email.ChallengeError:
				return "rejected"

		with ThreadPoolExecutor(max_workers=2) as pool:
			results = list(pool.map(lambda _: consume(), range(2)))
		self.assertCountEqual(results, ["/account", "rejected"])
		self.assertEqual(self.verify(second), "/account")

	def test_native_failure_burns_code_and_clears_cookie(self):
		args = self.challenge()
		with db("id.localhost"), patch("frappe.auth.clear_cookies") as clear:
			frappe.local.session = frappe._dict(user="Guest")
			frappe.local.login_manager = SimpleNamespace(
				login_as=Mock(side_effect=RuntimeError("unavailable"))
			)
			with self.assertRaises(RuntimeError):
				email.verify(*args, "1")
			clear.assert_called_once()
		with self.assertRaisesRegex(email.ChallengeError, "ended"):
			self.verify(args)

	def test_native_insertion_rechecks_revocation(self):
		with db("id.localhost"):
			frappe.flags.passport_email_auth = {"user": USER, "generation": -1, "auth_time": int(time.time())}
			session = SimpleNamespace(user=USER, data=frappe._dict(data=frappe._dict()))
			with self.assertRaises(frappe.AuthenticationError):
				account.insert(session)

	def test_delivery_failure_does_not_extend_code(self):
		args = self.challenge(mail=["test-queue"])
		with db("id.localhost"), patch.object(frappe, "get_doc") as get_doc:
			before = store.get("email_challenge", args[0])
			get_doc.return_value.send.side_effect = RuntimeError("SMTP offline")
			with self.assertRaises(RuntimeError):
				email.deliver(args[0], 1, "test-queue")
			self.assertEqual(store.get("email_challenge", args[0]), before)
			get_doc.reset_mock()
			email.deliver(args[0], 0, "test-queue")
			email.deliver(args[0], 1, "another-queue")
			get_doc.assert_not_called()

	def test_queue_failure_rolls_back_challenge(self):
		attempt = secrets.token_hex(16)
		self.attempts.append(attempt)
		with (
			db("id.localhost"),
			patch.object(email, "limit_issuance"),
			patch.object(frappe, "sendmail", side_effect=RuntimeError("queue offline")),
		):
			with self.assertRaises(RuntimeError):
				email.issue(attempt, "browser", USER, "/account")
			frappe.db.rollback()
			self.assertIsNone(store.get("email_challenge", attempt))

	def test_cleanup_preserves_queue_for_late_provider_callbacks(self):
		active = self.challenge()
		expired = self.challenge(deadline=0)
		with db("id.localhost"):
			queue = frappe.sendmail(
				recipients=["cleanup-test@example.invalid"],
				sender=frappe.conf.get("passport_email_sender"),
				subject="Queue retention fixture",
				message="Synthetic content; no login code.",
				delayed=True,
				add_unsubscribe_link=0,
			)
			try:
				queue.db_set("status", "Sent")
				row = store.get("email_challenge", expired[0])
				row["mail"] = [queue.name]
				store.put("email_challenge", expired[0], row)
				frappe.db.commit()
				email.cleanup()
				self.assertIsNone(store.get("email_challenge", expired[0]))
				self.assertIsNotNone(store.get("email_challenge", active[0]))
				name = frappe.db.get_value("Email Queue", {"message_id": queue.message_id}, "name")
				self.assertEqual(name, queue.name)
				retained = frappe.get_doc("Email Queue", name)
				self.assertEqual(len(retained.recipients), 1)
				retained.db_set("status", "Error")
				self.assertEqual(frappe.db.get_value("Email Queue", name, "status"), "Error")
			finally:
				frappe.db.delete("Email Queue Recipient", {"parent": queue.name})
				frappe.db.delete("Email Queue", {"name": queue.name})

	def test_rate_limits_survive_native_cache_clear(self):
		from frappe.cache_manager import clear_cache

		with db("id.localhost"):
			prefix = "passport:email-limit:"
			self.assertIn(prefix, frappe.get_hooks("persistent_cache_keys"))
			nonce = secrets.token_hex(16)
			counter = frappe.cache.make_key(prefix + nonce)
			ordinary = frappe.cache.make_key("passport-ui-test:" + nonce)
			frappe.cache.set(counter, 3, ex=3600)
			frappe.cache.set(ordinary, "test", ex=3600)
			get_keys = frappe.cache.get_keys

			def isolated_keys(key, *args, **kwargs):
				# Exercise native clearing without touching real acceptance counters.
				return [item for item in get_keys(key, *args, **kwargs) if item in (counter, ordinary)]

			try:
				with patch.object(frappe.cache, "get_keys", side_effect=isolated_keys):
					clear_cache()
				self.assertEqual(frappe.cache.get(counter), b"3")
				self.assertGreater(frappe.cache.ttl(counter), 3500)
				self.assertIsNone(frappe.cache.get(ordinary))
			finally:
				frappe.cache.delete(counter, ordinary)

	def test_real_rate_limits_and_fail_closed(self):
		prefix = "email-test-" + secrets.token_hex(16)
		keys = []
		with (
			db("id.localhost"),
			patch.object(email, "keyed_digest", side_effect=lambda kind, value: prefix + kind + value),
		):
			frappe.local.request_ip = "test-ip"
			try:
				for n in range(4):
					address = f"{n}@example.test"
					keys.append(frappe.cache.make_key("passport:email-limit:" + prefix + "email" + address))
					for _ in range(5):
						email.limit_issuance(address)
					with self.assertRaises(email.ChallengeError):
						email.limit_issuance(address)
				with self.assertRaises(email.ChallengeError):
					email.limit_issuance("new@example.test")
				with patch.object(frappe.cache, "eval", side_effect=ConnectionError):
					with self.assertRaises(ConnectionError):
						email.limit_issuance("new@example.test")
			finally:
				keys.append(frappe.cache.make_key("passport:email-limit:" + prefix + "iptest-ip"))
				frappe.cache.delete(*keys)


if __name__ == "__main__":
	unittest.main(verbosity=2)
