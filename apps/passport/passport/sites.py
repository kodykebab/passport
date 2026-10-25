"""Authenticated navigation to previously authorized sites."""

import frappe
from passport_core.ui import render_page
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from passport import connections, store


def response():
	if frappe.request.path != "/sites":
		return None
	if frappe.request.method != "GET":
		return Response(status=405)
	if frappe.session.user == "Guest" or not store.get_valid_native_session(
		frappe.session.sid, frappe.session.user
	):
		return redirect("/login?redirect-to=/sites")
	return Response(
		render_page(
			"passport",
			"Your sites",
			{"page": "sites", "title": "Your sites", "sites": connections.sites(frappe.session.user)},
		),
		content_type="text/html",
		headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
	)
