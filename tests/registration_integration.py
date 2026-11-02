import json
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import frappe
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from passport import oidc, protocol, registration, setup, store
from werkzeug.wrappers import Request


class RegistrationTests(unittest.TestCase):
	def test_enrollment_rejects_private_or_malformed_public_material(self):
		request = {
			"issuer": oidc.issuer(),
			"client_id": self.identifier,
			"home_url": "https://enrollment.example.invalid",
			"public_jwk": protocol.public_jwk(self.private_key, "fixture"),
		}
		for key, value in (("d", "private"), ("x", "bad"), ("kid", None)):
			with self.subTest(key=key):
				invalid = {**request, "public_jwk": {**request["public_jwk"], key: value}}
				with self.assertRaises(frappe.ValidationError):
					setup.approve_enrollment(invalid, "Invalid fixture")
		for identifier in (None, [], "", "x" * 141):
			with self.assertRaises(frappe.ValidationError):
				setup.approve_enrollment({**request, "client_id": identifier}, "Invalid fixture")

	def setUp(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		self.enterContext(patch.dict(frappe.conf, {"passport_issuer": "https://issuer.example.invalid"}))
		self.identifier = "registration-" + uuid.uuid4().hex
		self.private_key = (
			Ed25519PrivateKey.generate()
			.private_bytes(
				serialization.Encoding.PEM,
				serialization.PrivateFormat.PKCS8,
				serialization.NoEncryption(),
			)
			.decode()
		)
		self.client = frappe.get_doc(
			{
				"doctype": "Passport Client",
				"client_id": self.identifier,
				"title": "Registration fixture",
				"public_jwk": json.dumps(protocol.public_jwk(self.private_key, "fixture")),
				"home_url": "https://site.example.invalid",
				"redirect_uri": "https://site.example.invalid/passport/callback",
				"enabled": 1,
			}
		).insert()
		self.addCleanup(frappe.db.rollback)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_trust_changes_rotate_revision_but_metadata_does_not(self):
		current = self.client.registration_revision
		self.assertTrue(current)
		self.client.title = "Renamed fixture"
		self.client.registration_revision = "forged"
		self.client.save()
		self.assertEqual(self.client.registration_revision, current)
		for field, value in (
			("enabled", 0),
			("enabled", 1),
			("home_url", "https://new.example.invalid"),
			("redirect_uri", "https://new.example.invalid/passport/callback"),
			("public_jwk", json.dumps(protocol.public_jwk(self.private_key, "replacement"))),
		):
			self.client.set(field, value)
			self.client.save()
			self.assertNotEqual(self.client.registration_revision, current)
			with self.assertRaises(ValueError):
				registration.check(self.identifier, current)
			current = self.client.registration_revision

	def test_non_manager_cannot_register(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			setup.register_client(self.identifier, "unauthorized", "{}", "https://other.example.invalid")

	def test_replacement_waits_for_inflight_protocol_transaction(self):
		frappe.db.commit()
		started, finished = threading.Event(), threading.Event()
		errors = []
		sites = str(Path(frappe.local.sites_path).resolve())

		def replace():
			try:
				frappe.init(site="test.local", sites_path=sites)
				frappe.connect()
				frappe.set_user("Administrator")
				document = frappe.get_doc("Passport Client", self.identifier)
				document.enabled = 0
				started.set()
				document.save()
				frappe.db.commit()
			except Exception as error:
				errors.append(error)
			finally:
				frappe.destroy()
				finished.set()

		thread = threading.Thread(target=replace)
		try:
			registration.check(self.identifier, self.client.registration_revision)
			thread.start()
			self.assertTrue(started.wait(5))
			self.assertFalse(finished.wait(0.2))
			frappe.db.commit()
			self.assertTrue(finished.wait(5))
			self.assertFalse(errors, errors)
			with self.assertRaises(ValueError):
				registration.check(self.identifier, self.client.registration_revision)
		finally:
			frappe.db.rollback()
			thread.join(timeout=5)
			frappe.delete_doc("Passport Client", self.identifier)
			frappe.db.commit()

	def test_old_grant_cannot_cross_registration_change(self):
		code = uuid.uuid4().hex
		key = protocol.digest(code.encode())
		store.create(
			"grant",
			key,
			{
				"client": self.identifier,
				"user": "Administrator",
				"issuer_sid": "fixture-session",
				"registration_revision": self.client.registration_revision,
				"redirect_uri": self.client.redirect_uri,
				"challenge": protocol.pkce_challenge("fixture-verifier"),
				"exp": time.time() + 60,
			},
		)
		self.client.enabled = 0
		self.client.save()
		self.client.enabled = 1
		self.client.save()
		with patch.object(store, "get_valid_native_session", return_value={"valid": True}):
			with self.assertRaisesRegex(ValueError, "client_registration_changed"):
				oidc.token(
					self.client,
					{
						"code": code,
						"code_verifier": "fixture-verifier",
						"redirect_uri": self.client.redirect_uri,
					},
				)
		self.assertFalse(store.get("grant", key).get("spent"))

	def test_renewal_rechecks_authenticated_snapshot_and_login_revision(self):
		original = self.client.registration_revision
		login = {
			"client": self.identifier,
			"user": "Administrator",
			"issuer_sid": "fixture-session",
			"registration_revision": original,
			"revoked": False,
			"deadline": time.time() + 3600,
			"sub": "fixture-subject",
			"binding": "fixture-binding",
		}
		store.create("login", self.identifier, login)
		body = {"sid": self.identifier, "binding": "fixture-binding", "operation": "fixture-operation"}
		with (
			patch.object(store, "get_valid_native_session", return_value={"valid": True}),
			patch.object(oidc, "signing", return_value="fixture-lease"),
		):
			self.assertEqual(oidc.lease(self.client, body), {"lease": "fixture-lease"})
			self.client.home_url = "https://replacement.example.invalid"
			self.client.save()
			with self.assertRaisesRegex(ValueError, "client_registration_changed"):
				oidc.lease(self.client, body)
			login["registration_revision"] = self.client.registration_revision
			store.put("login", self.identifier, login)
			self.client.registration_revision = original
			with self.assertRaisesRegex(ValueError, "client_registration_changed"):
				oidc.lease(self.client, body)

	def test_signed_assertion_authentication_carries_current_revision(self):
		body = json.dumps({"client_id": self.identifier}).encode()
		endpoint = oidc.issuer() + "/passport/renew"
		assertion = protocol.assertion(self.identifier, endpoint, body, self.private_key, "fixture")
		frappe.local.request = Request.from_values(
			path="/passport/renew",
			method="POST",
			data=body,
			content_type="application/json",
			headers={"Passport-Client-Assertion": assertion},
		)
		client, _ = oidc.authenticate()
		self.assertEqual(client.registration_revision, self.client.registration_revision)
		self.client.public_jwk = json.dumps(protocol.public_jwk(self.private_key, "new-key-id"))
		self.client.save()
		with self.assertRaises(ValueError):
			oidc.authenticate()


if __name__ == "__main__":
	sites = Path("/tmp/passport-issuer-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	frappe.init(site="test.local", sites_path=str(sites))
	frappe.connect()
	frappe.in_test = True
	try:
		frappe.set_user("Administrator")
		frappe.reload_doc("passport", "doctype", "passport_client", force=True)
		frappe.db.commit()
		result = unittest.TextTestRunner(verbosity=2).run(
			unittest.defaultTestLoader.loadTestsFromTestCase(RegistrationTests)
		)
		if not result.wasSuccessful():
			raise SystemExit(1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
