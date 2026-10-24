"""Resolve the shared public UI build independently on each installed app."""

import json
from pathlib import Path

import frappe


def assets(app):
	manifest = Path(frappe.get_app_path(app, "public", "ui", "manifest.json"))
	entry = json.loads(manifest.read_text())["src/main.js"]
	prefix = f"/assets/{app}/ui/"
	return {"script": prefix + entry["file"], "style": prefix + entry["css"][0]}
