import base64
import time
import unittest

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from passport import protocol as issuer
from passport_client import protocol as client


class ProtocolTests(unittest.TestCase):
	def setUp(self):
		self.pem = (
			Ed25519PrivateKey.generate()
			.private_bytes(
				serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
			)
			.decode()
		)
		self.key = issuer.public_jwk(self.pem, "test")
		self.claims = {"iss": "issuer", "aud": "site", "iat": int(time.time()), "exp": int(time.time()) + 60}

	def test_roundtrip(self):
		token = issuer.sign(self.claims, self.pem, "test", "passport-id+jwt")
		self.assertEqual(client.verify(token, self.key, "passport-id+jwt", "issuer", "site"), self.claims)

	def test_wrong_trust_and_purpose(self):
		token = issuer.sign(self.claims, self.pem, "test", "passport-id+jwt")
		for purpose, iss, aud in [
			("passport-lease+jwt", "issuer", "site"),
			("passport-id+jwt", "other", "site"),
			("passport-id+jwt", "issuer", "other"),
		]:
			with self.assertRaises((ValueError, jwt.InvalidTokenError)):
				client.verify(token, self.key, purpose, iss, aud)

	def test_hs256_rejected(self):
		token = jwt.encode(
			self.claims, "a" * 32, algorithm="HS256", headers={"kid": "test", "typ": "passport-id+jwt"}
		)
		with self.assertRaises(ValueError):
			client.verify(token, self.key, "passport-id+jwt", "issuer", "site")

	def test_expiry_no_leeway(self):
		self.claims["iat"] -= 61
		self.claims["exp"] -= 61
		token = issuer.sign(self.claims, self.pem, "test", "passport-id+jwt")
		with self.assertRaises(jwt.ExpiredSignatureError):
			client.verify(token, self.key, "passport-id+jwt", "issuer", "site")

	def test_foreign_curve_and_key(self):
		token = issuer.sign(self.claims, self.pem, "test", "passport-id+jwt")
		with self.assertRaises(ValueError):
			client.verify(token, {**self.key, "crv": "Ed448"}, "passport-id+jwt", "issuer", "site")
		other = (
			Ed25519PrivateKey.generate()
			.private_bytes(
				serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
			)
			.decode()
		)
		with self.assertRaises(jwt.InvalidSignatureError):
			client.verify(token, issuer.public_jwk(other, "test"), "passport-id+jwt", "issuer", "site")

	def test_pkce_known_vector(self):
		self.assertEqual(
			issuer.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
			"E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
		)

	def test_extra_header_rejected(self):
		token = jwt.encode(
			self.claims,
			self.pem,
			algorithm="EdDSA",
			headers={"kid": "test", "typ": "passport-id+jwt", "jku": "https://example.com/keys"},
		)
		with self.assertRaises(ValueError):
			client.verify(token, self.key, "passport-id+jwt", "issuer", "site")

	def test_duplicate_json_claims_rejected(self):
		token = issuer.sign(self.claims, self.pem, "test", "passport-id+jwt")
		header, _, signature = token.split(".")
		payload = base64.urlsafe_b64encode(b'{"iss":"issuer","iss":"other"}').rstrip(b"=").decode()
		with self.assertRaisesRegex(ValueError, "Duplicate JSON member"):
			client.verify(f"{header}.{payload}.{signature}", self.key, "passport-id+jwt", "issuer", "site")

	def test_invalid_time_types_and_order(self):
		for changes in (
			{"iat": True},
			{"iat": str(self.claims["iat"])},
			{"exp": str(self.claims["exp"])},
			{"iat": self.claims["exp"] + 10},
		):
			with self.subTest(changes=changes):
				token = issuer.sign({**self.claims, **changes}, self.pem, "test", "passport-id+jwt")
				with self.assertRaises(ValueError):
					client.verify(token, self.key, "passport-id+jwt", "issuer", "site")


if __name__ == "__main__":
	unittest.main()
