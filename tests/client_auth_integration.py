import json
from pathlib import Path

import requests
from passport_client.protocol import assertion

root = Path(__file__).resolve().parents[1]
config = json.loads((root / ".bench/sites/site1.localhost/site_config.json").read_text())
endpoint = config["passport_issuer"] + "/oidc/token"
body = json.dumps({"client_id": "site1.localhost", "code": "not-a-code"}).encode()


def headers(a):
	return {"Content-Type": "application/json", "Passport-Client-Assertion": a}


a = assertion("site1.localhost", endpoint, body, config["passport_private_key"], config["passport_key_id"])
r = requests.post(endpoint, data=body, headers=headers(a))
assert r.status_code == 403, "valid authentication must reach grant validation"
r = requests.post(endpoint, data=body, headers=headers(a))
assert r.status_code == 401, "replay must fail authentication"
print("PASS durable client assertion replay rejection")
a = assertion("site1.localhost", endpoint, body, config["passport_private_key"], config["passport_key_id"])
r = requests.post(endpoint, data=body + b" ", headers=headers(a))
assert r.status_code == 401
print("PASS exact-body binding")
a = assertion(
	"site1.localhost", endpoint + "/wrong", body, config["passport_private_key"], config["passport_key_id"]
)
assert requests.post(endpoint, data=body, headers=headers(a)).status_code == 401
print("PASS exact endpoint audience")
assert requests.post(endpoint + "?cmd=login", data=body, headers=headers(a)).status_code == 400
print("PASS protocol cmd override rejected")
assert requests.post(endpoint + "?sid=Guest", data=body, headers=headers(a)).status_code == 400
print("PASS protocol session parameter rejected")

for malformed in (
	[],
	{"client_id": []},
	{"client_id": "site1.localhost", "code": 123},
	{"client_id": "site1.localhost", "deadline": True},
):
	r = requests.post(endpoint, json=malformed, timeout=10)
	assert r.status_code == 401, (malformed, r.status_code)
print("PASS malformed protocol field types rejected without server errors")
