"""Small Ed25519-only wire helpers. No remote or token-selected key URLs."""

import base64
import hashlib
import json
import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def digest(value):
	return hashlib.sha256(value).hexdigest()


def pkce_challenge(verifier):
	return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def public_jwk(pem, kid):
	key = serialization.load_pem_private_key(pem.encode(), password=None)
	if not isinstance(key, Ed25519PrivateKey):
		raise ValueError("Ed25519 required")
	x = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
	return {
		"kty": "OKP",
		"crv": "Ed25519",
		"alg": "EdDSA",
		"use": "sig",
		"kid": kid,
		"x": base64.urlsafe_b64encode(x).rstrip(b"=").decode(),
	}


def sign(claims, pem, kid, purpose):
	key = serialization.load_pem_private_key(pem.encode(), password=None)
	if not isinstance(key, Ed25519PrivateKey):
		raise ValueError("Ed25519 required")
	return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid, "typ": purpose})


def verify(token, jwk, purpose, issuer, audience, required=()):
	def unique(pairs):
		result = {}
		for key, value in pairs:
			if key in result:
				raise ValueError("Duplicate JSON member")
			result[key] = value
		return result

	if not isinstance(token, str) or len(token) > 16384:
		raise ValueError("Invalid token size")
	for part in token.split(".")[:2]:
		json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)), object_pairs_hook=unique)
	header = jwt.get_unverified_header(token)
	if header != {"alg": "EdDSA", "typ": purpose, "kid": jwk["kid"]}:
		raise ValueError("Invalid token header")
	if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
		raise ValueError("Invalid key")
	key = jwt.PyJWK.from_dict(jwk).key
	if not isinstance(key, Ed25519PublicKey):
		raise ValueError("Invalid key")
	claims = jwt.decode(
		token,
		key,
		algorithms=["EdDSA"],
		issuer=issuer,
		audience=audience,
		options={"require": ["iss", "aud", "iat", "exp", *required], "verify_iat": False},
	)
	if (
		type(claims["iat"]) is not int
		or type(claims["exp"]) is not int
		or claims["iat"] > time.time() + 5
		or claims["exp"] <= claims["iat"]
	):
		raise ValueError("Invalid times")
	return claims


def assertion(client, endpoint, body, pem, kid):
	now = int(time.time())
	return sign(
		{
			"iss": client,
			"sub": client,
			"aud": endpoint,
			"iat": now,
			"exp": now + 60,
			"jti": secrets.token_hex(24),
			"body_sha256": digest(body),
		},
		pem,
		kid,
		"passport-client+jwt",
	)
