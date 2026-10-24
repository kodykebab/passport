"""Resolve the shared public UI build independently on each installed app."""

import json
from pathlib import Path

import frappe


def assets(app):
	manifest = Path(frappe.get_app_path(app, "public", "ui", "manifest.json"))
	entry = json.loads(manifest.read_text())["src/main.js"]
	prefix = f"/assets/{app}/ui/"
	return {"script": prefix + entry["file"], "style": prefix + entry["css"][0]}


def render_page(app, title, data):
	return frappe.render_template(
		Path(__file__).with_name("templates").joinpath("page.html").read_text(),
		{"ui": assets(app), "title": title, "data": data},
		is_path=False,
	)
