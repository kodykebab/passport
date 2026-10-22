"""Run with the existing bench Python; no new sites, users, or identity links."""

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

root = Path(__file__).resolve().parents[1]


def key():
	return (
		Ed25519PrivateKey.generate()
		.private_bytes(
			serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
		)
		.decode()
	)


for site, settings in {
	"id.localhost": {"passport_signing_key": key(), "passport_signing_kid": "issuer-dev-1"},
	"site1.localhost": {"passport_private_key": key(), "passport_key_id": "site1-dev-1"},
}.items():
	path = root / ".bench/sites" / site / "site_config.json"
	config = json.loads(path.read_text())
	for name, value in settings.items():
		config.setdefault(name, value)
	config.pop("passport_client_secret", None)
	path.write_text(json.dumps(config, indent=2) + "\n")
	os.chmod(path, 0o600)
print("Provisioned existing issuer and site1 key configuration; private keys were not printed.")
