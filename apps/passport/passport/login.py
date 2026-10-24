"""Public Frappe ID password UI; native Frappe still authenticates credentials."""

from urllib.parse import urlencode

import frappe
from passport_core.ui import assets
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport.email_login import return_path


def response():
	if frappe.request.path != "/login":
		return None
	if frappe.request.method != "GET":
		return Response(status=405)
	destination = return_path(frappe.request.args.get("redirect-to"))
	if frappe.session.user != "Guest":
		return redirect(destination)
	return Response(
		frappe.render_template(
			"templates/login.html",
			{
				"ui": assets("passport"),
				"destination": destination,
				"password_enabled": not frappe.get_system_settings("disable_user_pass_login"),
				"email_url": "/passport/email?" + urlencode({"redirect-to": destination})
				if frappe.conf.get("passport_email_code_key")
				else None,
			},
		),
		content_type="text/html",
		headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
	)
