"""Browser-bound email challenges for existing issuer users."""

import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import frappe
from passport_core.ui import assets
from werkzeug.exceptions import BadRequest
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport import protocol, store

PATH = "/passport/email"
CODE_LIFETIME = 300
ATTEMPT_LIFETIME = 900
RESEND_COOLDOWN = 60
MAX_FAILURES = 5


class ChallengeError(Exception):
	pass


def keyed_digest(*parts):
	key = frappe.conf.get("passport_email_code_key")
	if not key:
		raise ChallengeError("Email login is unavailable. Use password login or try again later.")
	return hmac.new(key.encode(), "\0".join(str(part) for part in parts).encode(), hashlib.sha256).hexdigest()


def return_path(path):
	if not isinstance(path, str) or "\\" in path or any(ord(c) < 32 for c in path):
		return "/account"
	try:
		url = urlsplit(path)
	except ValueError:
		return "/account"
	if url.scheme or url.netloc or url.path not in ("/oidc/authorize", "/account"):
		return "/account"
	return path


def boundary():
	if frappe.request.path != PATH:
		return
	if frappe.request.content_length and frappe.request.content_length > 4096:
		raise BadRequest("Request too large")
	if frappe.request.is_json or any(k in frappe.form_dict for k in ("cmd", "sid")):
		raise BadRequest("Invalid login parameters")
	if any(
		len(values.getlist(k)) != 1 for values in (frappe.request.args, frappe.request.form) for k in values
	):
		raise BadRequest("Duplicate parameters")


def limit_issuance(email):
	# One Redis operation checks and increments both limits, including unknown users.
	keys = [
		frappe.cache.make_key("passport:email-limit:" + keyed_digest(kind, value))
		for kind, value in (("email", email), ("ip", frappe.local.request_ip))
	]
	allowed = frappe.cache.eval(
		"""
		for i,key in ipairs(KEYS) do
			if tonumber(redis.call('GET', key) or '0') >= tonumber(ARGV[i]) then return 0 end
		end
		for _,key in ipairs(KEYS) do
			if redis.call('INCR', key) == 1 then redis.call('EXPIRE', key, 3600) end
		end
		return 1
	""",
		2,
		*keys,
		5,
		20,
	)
	if not allowed:
		raise ChallengeError("Too many code requests. Please wait before trying again.")


def lock_and_check_user(user, email):
	if not user or user in ("Guest", "Administrator") or frappe.get_system_settings("enable_two_factor_auth"):
		return False
	rows = frappe.db.sql("SELECT enabled,email FROM tabUser WHERE name=%s FOR UPDATE", user)
	return bool(rows and rows[0][0] and rows[0][1].strip().lower() == email)


def lock_challenge(attempt, browser):
	row = store.get("email_challenge", attempt)
	if not row or not hmac.compare_digest(row["browser"], browser):
		raise ChallengeError("This attempt has ended. Start a new login.")
	# Discard the initial read snapshot before current locking reads on MariaDB.
	frappe.db.commit()
	valid_user = lock_and_check_user(row["user"], row["email"])
	current_generation = store.generation(row["user"], lock=True) if valid_user else None
	row = store.get("email_challenge", attempt, lock=True)
	if (
		not row
		or not hmac.compare_digest(row["browser"], browser)
		or row["spent"]
		or row["deadline"] <= time.time()
		or row["failures"] >= MAX_FAILURES
	):
		raise ChallengeError("This attempt has ended. Start a new login.")
	return row, valid_user and row["user_generation"] == current_generation


def queue_code(attempt, row, code):
	queue = frappe.sendmail(
		recipients=[row["email"]],
		sender=frappe.conf.get("passport_email_sender"),
		subject="Your Frappe ID login code",
		template="passport_login_code",
		args={"code": code, "request": attempt[:8], "generation": row["generation"]},
		delayed=True,
		add_unsubscribe_link=0,
	)
	if not queue:
		raise ChallengeError("Email login is unavailable. Please try again later.")
	row["mail"].append(queue.name)
	frappe.enqueue(
		"passport.email_login.deliver",
		attempt=attempt,
		generation=row["generation"],
		name=queue.name,
		enqueue_after_commit=True,
	)


def deliver(attempt, generation, name):
	"""Internal worker entry point; only deliver a queue belonging to this challenge."""
	row = store.get("email_challenge", attempt)
	if (
		not row
		or row["spent"]
		or row["expires"] <= time.time()
		or row["generation"] != generation
		or name not in row["mail"]
	):
		return
	frappe.get_doc("Email Queue", name).send()


def issue(attempt, browser, email, destination):
	email = email.strip().lower()
	if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 140:
		raise ChallengeError("Enter a valid email address.")
	limit_issuance(email)
	user = frappe.db.get_value("User", {"email": email}, "name")
	frappe.db.commit()
	valid_user = lock_and_check_user(user, email)
	now = int(time.time())
	row = {
		"user": user,
		"email": email,
		"browser": browser,
		"return": return_path(destination),
		"user_generation": store.generation(user, lock=True) if valid_user else None,
		"deadline": now + ATTEMPT_LIFETIME,
		"failures": 0,
		"spent": False,
		"generation": 0,
		"mail": [],
	}
	set_code(attempt, row, valid_user)
	store.create("email_challenge", attempt, row)
	frappe.db.commit()


def set_code(attempt, row, valid_user):
	code = f"{secrets.randbelow(1000000):06d}"
	while row.get("digest") == keyed_digest(attempt, row["generation"], code):
		code = f"{secrets.randbelow(1000000):06d}"
	now = int(time.time())
	row["generation"] += 1
	row["digest"] = keyed_digest(attempt, row["generation"], code)
	row["expires"] = min(now + CODE_LIFETIME, row["deadline"])
	row["resend_after"] = now + RESEND_COOLDOWN
	if valid_user:
		queue_code(attempt, row, code)


def resend(attempt, browser):
	row, valid_user = lock_challenge(attempt, browser)
	if row["resend_after"] > time.time():
		raise ChallengeError("Please wait before requesting another code.")
	limit_issuance(row["email"])
	set_code(attempt, row, valid_user)
	store.put("email_challenge", attempt, row)
	frappe.db.commit()


def verify(attempt, browser, code, generation):
	row, valid_user = lock_challenge(attempt, browser)
	if row["expires"] <= time.time():
		raise ChallengeError("This code has expired. Request another code.")
	if str(row["generation"]) != generation:
		raise ChallengeError("A newer code was requested. Use the latest code on this page.")
	row["failures"] += 1
	valid_code = bool(re.fullmatch(r"[0-9]{6}", code)) and hmac.compare_digest(
		row["digest"], keyed_digest(attempt, row["generation"], code)
	)
	if not valid_code or not valid_user:
		store.put("email_challenge", attempt, row)
		frappe.db.commit()
		raise ChallengeError("Incorrect or unavailable code. Try again, or start a new login.")
	row["spent"] = True
	store.put("email_challenge", attempt, row)
	frappe.db.commit()
	frappe.flags.passport_email_auth = {
		"user": row["user"],
		"generation": row["user_generation"],
		"auth_time": int(time.time()),
	}
	try:
		frappe.local.login_manager.login_as(row["user"])
	except Exception:
		# Native creation may already have committed. Never leave an unusable cookie.
		from frappe.auth import clear_cookies
		from frappe.sessions import delete_session

		if frappe.session.user == row["user"]:
			delete_session(frappe.session.sid, user=row["user"], reason="Email login did not complete")
		clear_cookies()
		raise
	return return_path(row["return"])


def response():
	if frappe.request.path != PATH:
		return None
	if frappe.request.method not in ("GET", "POST"):
		return Response(status=405)
	if frappe.session.user != "Guest":
		return redirect(return_path(frappe.request.args.get("redirect-to")))
	attempt = (
		frappe.request.args.get("attempt")
		if frappe.request.method == "GET"
		else frappe.request.form.get("attempt")
	)
	fresh = not attempt and frappe.request.method == "GET"
	attempt = secrets.token_hex(16) if fresh else attempt
	if not attempt or not re.fullmatch(r"[a-f0-9]{32}", attempt):
		return Response("Invalid login attempt", status=400)
	cookie_name = "passport_email_" + attempt
	secret = secrets.token_urlsafe(32) if fresh else frappe.request.cookies.get(cookie_name, "")
	browser = protocol.digest(secret.encode())
	if not secret:
		return page(
			attempt, browser, error="This attempt belongs to another browser or has expired.", status=403
		)
	error, status = None, 200
	if frappe.request.method == "POST":
		if not hmac.compare_digest(frappe.request.form.get("form_token", ""), browser):
			return page(attempt, browser, error="Invalid form. Start a new login.", status=403)
		try:
			action = frappe.request.form.get("action")
			if action == "send":
				issue(
					attempt,
					browser,
					frappe.request.form.get("email", ""),
					frappe.request.form.get("redirect-to"),
				)
			elif action == "resend":
				resend(attempt, browser)
			elif action == "verify":
				destination = verify(
					attempt,
					browser,
					frappe.request.form.get("code", ""),
					frappe.request.form.get("generation", ""),
				)
				result = redirect(destination, code=303)
				result.delete_cookie(cookie_name, path=PATH)
				result.headers["Cache-Control"] = "no-store"
				return result
			else:
				raise ChallengeError("Invalid action.")
			return redirect(PATH + "?" + urlencode({"attempt": attempt}), code=303)
		except ChallengeError as exc:
			frappe.db.rollback()
			error, status = str(exc), 400
		except Exception:
			# Do not expose/log codes or delivery payloads in a developer-mode traceback.
			frappe.db.rollback()
			error, status = "Email login could not complete. Start a new login or use your password.", 503
	result = page(attempt, browser, error=error, status=status)
	if fresh:
		result.set_cookie(
			cookie_name,
			secret,
			max_age=ATTEMPT_LIFETIME,
			httponly=True,
			samesite="Lax",
			secure=frappe.request.scheme == "https",
			path=PATH,
		)
	return result


def page(attempt, browser, *, error=None, status=200):
	row = store.get("email_challenge", attempt)
	if row and not hmac.compare_digest(row["browser"], browser):
		row = None
	destination = return_path(row["return"] if row else frappe.request.args.get("redirect-to"))
	return Response(
		frappe.render_template(
			"templates/email_login.html",
			{
				"ui": assets("passport"),
				"attempt": attempt,
				"form_token": browser,
				"challenge": row,
				"error": error,
				"destination": destination,
				"password_url": "/login?" + urlencode({"redirect-to": destination}),
				"wait": max(0, row["resend_after"] - int(time.time())) if row else 0,
			},
		),
		status=status,
		content_type="text/html",
		headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
	)


def cleanup():
	# Native mail retention owns queue rows, including late provider status callbacks.
	for attempt, row in store.rows("email_challenge"):
		if row["deadline"] > time.time():
			continue
		frappe.db.sql("DELETE FROM passport_state WHERE kind='email_challenge' AND id=%s", attempt)
