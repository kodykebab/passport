"""Issuer HTTP actions and real SQL revocation races using isolated client records; no email."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import frappe
from live_support import ID, USER, BrowserSessions, check, db
from page_data import page_data
from passport import connections, oidc, protocol, store

prefix = "connections-test-" + secrets.token_hex(8)
clients = [prefix + "-one", prefix + "-two"]
records = []


def issue(sid, client, generation):
	login = prefix + "-" + secrets.token_hex(8)
	with db("id.localhost"):
		store.get_valid_native_session(sid, USER, lock=True)
		store.create(
			"login",
			login,
			{
				"client": client,
				"user": USER,
				"sub": "isolated-subject",
				"issuer_sid": sid,
				"revoked": False,
				"deadline": int(time.time()) + 3600,
				"pending_until": time.time() + 120,
				"connection_generation": generation,
			},
		)
	records.append(("login", login))
	return login


def lease(login, client):
	with db("id.localhost"):
		frappe.local.request = SimpleNamespace(path="/passport/complete")
		try:
			return oidc.lease(
				SimpleNamespace(name=client),
				{
					"sid": login,
					"binding": "test-binding",
					"operation": "test-operation",
					"deadline": int(time.time()) + 3600,
				},
			)
		except ValueError:
			frappe.db.rollback()
			return None


try:
	with BrowserSessions() as sessions:
		browser = sessions.browser()
		check(
			"dedicated issuer password login",
			browser.post(ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"}).ok,
		)
		sid = browser.cookies.get("sid", domain="id.localhost")
		with db("id.localhost"):
			assert store.get_valid_native_session(sid, USER, lock=True)
			for client in clients:
				connections.remember(USER, client)
		csrf = page_data(browser.get(ID + "/account").text)["csrf_token"]

		def action(action, *, password="alice-passport-1", token=csrf):
			return browser.post(
				ID + "/account",
				data={"action": action, "client": clients[0], "csrf_token": token, "password": password},
				allow_redirects=False,
			)

		first = issue(sid, clients[0], 0)
		pending = issue(sid, clients[0], 0)
		other = issue(sid, clients[1], 0)
		check("initial completion permitted", bool(lease(first, clients[0])))
		check("missing CSRF cannot revoke", action("revoke_site", token="").status_code in (400, 403))
		check(
			"wrong password stays inline",
			action("revoke_site", password="wrong-connections-password").status_code == 403,
		)
		check("failed confirmation preserves issuer authentication", browser.get(ID + "/account").ok)
		check("failed confirmation leaves renewal available", bool(lease(first, clients[0])))

		code = prefix + "-code"
		grant_key = protocol.digest(code.encode())
		with db("id.localhost"):
			store.create(
				"grant",
				grant_key,
				{
					"client": clients[0],
					"user": USER,
					"issuer_sid": sid,
					"connection_generation": 0,
					"redirect_uri": "https://example.invalid/callback",
					"exp": time.time() + 60,
					"challenge": protocol.pkce_challenge("verifier"),
				},
			)
		records.append(("grant", grant_key))
		check("fresh password revokes site", action("revoke_site").status_code == 302)
		check("existing session cannot renew", lease(first, clients[0]) is None)
		check("pending completion cannot establish access", lease(pending, clients[0]) is None)
		check("other site is unaffected", bool(lease(other, clients[1])))
		with db("id.localhost"):
			try:
				oidc.token(
					SimpleNamespace(name=clients[0]),
					{
						"code": code,
						"redirect_uri": "https://example.invalid/callback",
						"code_verifier": "verifier",
					},
				)
			except ValueError:
				frappe.db.rollback()
				check("pending code exchange rejected", True)
			else:
				raise AssertionError("Revoked code accepted")
		check("explicit allow accepted", action("allow_site").status_code == 302)
		check("allow does not revive previous session", lease(first, clients[0]) is None)
		fresh = issue(sid, clients[0], 1)
		check("new generation can establish access", bool(lease(fresh, clients[0])))
		with ThreadPoolExecutor(max_workers=2) as pool:
			renewal = pool.submit(lease, fresh, clients[0])
			revocation = pool.submit(action, "revoke_site")
			check("concurrent revocation completes", revocation.result(timeout=30).status_code == 302)
			renewal.result(timeout=30)
		check("renewal after revocation race is denied", lease(fresh, clients[0]) is None)
		check("allow after race accepted", action("allow_site").status_code == 302)
		check("allow after race cannot revive raced session", lease(fresh, clients[0]) is None)
finally:
	with db("id.localhost"):
		for kind, identifier in records:
			frappe.db.sql("DELETE FROM passport_state WHERE kind=%s AND id=%s", (kind, identifier))
		for client in clients:
			frappe.db.sql(
				"DELETE FROM passport_state WHERE kind='connection' AND id=%s", connections.key(USER, client)
			)
