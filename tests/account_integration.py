import hashlib

import frappe
from live_support import ID, USER, check, db, login, renew
from page_data import page_data

first, first_csrf, _ = login()
second, second_csrf, _ = login()
page = first.get(ID + "/account")
csrf = page_data(page.text)["csrf_token"]
with db():
	local_name = frappe.db.get_value("User", USER, "full_name")
with db("id.localhost"):
	issuer_name = frappe.db.get_value("User", USER, "full_name")


def action(values, token=csrf):
	return first.post(
		ID + "/account",
		data={**values, "csrf_token": token, "password": "alice-passport-1"},
		allow_redirects=False,
	)


check(
	"account mutation without CSRF rejected",
	action({"action": "name", "display_name": "Not applied"}, token="").status_code in (400, 403),
)
try:
	check(
		"issuer display-name edit accepted",
		action(
			{"action": "name", "display_name": "Alice profile test", "email": "ignored@example.com"}
		).status_code
		== 302,
	)
	with db():
		check("site-owned name preserved", frappe.db.get_value("User", USER, "full_name") == local_name)
	with db("id.localhost"):
		check("email remains read-only", frappe.db.get_value("User", USER, "email") == USER)
finally:
	action({"action": "name", "display_name": issuer_name})
target = hashlib.sha256(second.cookies.get("sid", domain="id.localhost").encode()).hexdigest()
check("selected device logout accepted", action({"action": "device", "device": target}).status_code == 302)
check("other device renewal denied", not renew(second, second_csrf).ok)
check("current device unaffected", renew(first, first_csrf).ok)
check("current account page remains available", first.get(ID + "/account").ok)
print("Account scope checks passed")
