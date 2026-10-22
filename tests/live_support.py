"""Real SQL/cache/HTTP lease tests; mutates only test browser sessions."""

import contextlib
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT / ".bench/sites")
import frappe
from passport_client import store as store

ID = "http://id.localhost:8080"
SITE = "http://site1.localhost:8080"
USER = "alice@example.com"


@contextlib.contextmanager
def db(site="site1.localhost"):
	frappe.init(site=site, sites_path=str(ROOT / ".bench/sites"))
	frappe.connect()
	try:
		yield
		frappe.db.commit()
	finally:
		frappe.destroy()


def check(label, value):
	assert value, label
	print("PASS", label, flush=True)


def login():
	b = requests.Session()
	r = b.get(SITE + "/passport/login")
	path = parse_qs(urlparse(r.url).query)["redirect-to"][0]
	assert b.post(ID + "/api/method/login", data={"usr": USER, "pwd": "alice-passport-1"}).ok
	assert b.get(ID + path).ok
	r = b.get(SITE + "/desk")
	csrf = re.search(r'csrf_token = "([^"]+)"', r.text).group(1)
	return b, csrf, b.cookies.get("sid", domain="site1.localhost")


def get(b):
	return b.get(SITE + "/api/method/frappe.auth.get_logged_user")


def renew(b, csrf):
	return b.post(SITE + "/passport/retry", headers={"X-Frappe-CSRF-Token": csrf})
