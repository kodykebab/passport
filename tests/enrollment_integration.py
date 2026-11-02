import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from passport import setup
from passport_client import admission
from pilot.core.site.config import read_site_config, suspend_passport
from pilot.core.site.passport import SiteEnrollment
from pilot.exceptions import BenchError, CommandError


def run(issuer_url, client_url, user, connect, check):
	sites = Path("/tmp/passport-acceptance-20261029/sites")
	assert (
		sites / "test.local/.passport-acceptance-fixture"
	).read_text().strip() == "disposable-passport-test-site"
	site = SimpleNamespace(
		path=sites / "test.local",
		config=SimpleNamespace(name="test.local"),
		bench=SimpleNamespace(python=Path(sys.executable), sites_path=sites),
	)
	site._frappe_call = lambda *args: [sys.executable, "-m", "frappe.utils.bench_helper", *args]
	enrollment = SiteEnrollment(site)
	request = enrollment.prepare(issuer_url, "acceptance-client")
	check("private_key" not in json.dumps(request), "enrollment request contains public material only")
	check(
		enrollment.prepare(issuer_url, "acceptance-client") == request,
		"pending enrollment is stable on retry",
	)
	connect("client")
	reviewed = admission.preview()
	approved = frappe.get_all("Passport Identity", filters={"user": user}, pluck="name")
	frappe.db.commit()
	try:
		enrollment.confirm(reviewed, approved)
	except CommandError:
		pass
	else:
		raise AssertionError("unapproved key activated")
	check(read_site_config(site.path)["passport_disabled"], "unapproved key cannot activate site")
	connect("issuer")
	setup.approve_enrollment(request, "Enrollment Acceptance")
	connect("client")
	try:
		enrollment.confirm({}, [])
	except CommandError:
		pass
	else:
		raise AssertionError("stale admission review activated")
	check(read_site_config(site.path)["passport_disabled"], "stale admission review leaves site suspended")
	installation = enrollment.confirm(reviewed, approved)
	connect("client")
	check(
		frappe.conf.passport_installation == installation and not frappe.conf.passport_disabled,
		"signed administrator approval activates the matching installation",
	)
	check(admission.preview().keys() == reviewed.keys(), "enrollment preserves identity associations")
	check(
		frappe.get_all("Passport Identity", filters={"admitted": 1}, pluck="name") == approved,
		"enrollment grants only the reviewed selection",
	)
	frappe.db.commit()
	suspend_passport(sites, "test.local")
	request = enrollment.prepare(issuer_url, "acceptance-client")
	connect("issuer")
	setup.approve_enrollment(request, "Enrollment Acceptance")
	connect("client")
	reviewed = admission.preview()
	frappe.db.commit()
	from pilot.core.site import passport as pilot_passport

	real = pilot_passport.run_command

	def interrupted(*args, **kwargs):
		result = real(*args, **kwargs)
		suspend_passport(sites, "test.local")
		return result

	with patch.object(pilot_passport, "run_command", side_effect=interrupted):
		try:
			enrollment.confirm(reviewed, approved)
		except BenchError:
			pass
		else:
			raise AssertionError("intervening suspension was overwritten")
	check(
		read_site_config(site.path)["passport_disabled"],
		"restore during real confirmation cannot enable site",
	)
	request = enrollment.prepare(issuer_url, "acceptance-client")
	connect("issuer")
	setup.approve_enrollment(request, "Enrollment Acceptance")
	connect("client")
	reviewed = admission.preview()
	frappe.db.commit()
	enrollment.confirm(reviewed, approved)
	check(
		not read_site_config(site.path)["passport_disabled"],
		"fresh approval recovers after interrupted activation",
	)
	from frappe.utils.backups import backup
	from pilot.config import MariaDBConfig
	from pilot.core.site.commands import SiteCommands

	connect("client")
	before_restore = read_site_config(site.path)["passport_installation"]
	backup_file = backup()["backup_path_db"]
	frappe.db.commit()
	frappe.destroy()
	site.db_type = "mariadb"
	site.bench.path = sites.parent
	site.bench.config = SimpleNamespace(
		mariadb=MariaDBConfig(host="127.0.0.1", port=3327, root_password="root", existing=True)
	)
	site.suspend_passport = lambda: suspend_passport(sites, "test.local")
	SiteCommands(site).restore(backup_file)
	connect("client")
	check(
		frappe.conf.passport_disabled and frappe.conf.passport_installation != before_restore,
		"real database restore preserves the independent installation fence",
	)
	from passport_client import store

	check(
		store.get("admission_policy", "site")["installation"] == before_restore,
		"restored database carries the old policy and cannot authorize the new installation",
	)
	identity = frappe.get_doc("Passport Identity", approved[0])
	check(
		not admission.permits(
			{
				"association_id": identity.name,
				"access_epoch": identity.access_epoch,
				"issuer": identity.issuer,
				"sub": identity.subject,
				"user": identity.user,
			}
		),
		"restored approved identity is denied until fresh enrollment",
	)
	frappe.db.commit()
	request = enrollment.prepare(issuer_url, "acceptance-client")
	connect("issuer")
	setup.approve_enrollment(request, "Enrollment Acceptance")
	connect("client")
	reviewed = enrollment.review()["reviewed"]
	frappe.db.commit()
	enrollment.confirm(reviewed, approved)
	check(
		not read_site_config(site.path)["passport_disabled"],
		"restored site recovers through explicit review and approval",
	)
	suspend_passport(sites, "test.local")
	request = enrollment.prepare(issuer_url, "acceptance-client")
	connect("issuer")
	setup.approve_enrollment(request, "Enrollment Acceptance")
	connect("client")
	reviewed = admission.preview()
	frappe.db.commit()
	with patch.object(
		pilot_passport, "replace_private_text_locked", side_effect=OSError("fixture write failure")
	):
		try:
			enrollment.confirm(reviewed, approved)
		except OSError:
			pass
		else:
			raise AssertionError("fixture configuration failure did not occur")
	check(
		read_site_config(site.path)["passport_disabled"],
		"configuration write failure leaves committed admission suspended",
	)
	connect("client")
	committed = admission.preview()
	frappe.db.commit()
	enrollment.confirm(reviewed, approved)
	connect("client")
	check(
		admission.preview() == committed and not frappe.conf.passport_disabled,
		"confirmation retry recovers committed admission without applying it twice",
	)
	frappe.db.commit()
