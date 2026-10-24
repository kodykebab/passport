"""The shared shell preserves page data without exposing it as executable HTML."""

import unittest
from unittest.mock import patch

from jinja2 import Environment
from page_data import page_data
from passport_core import ui


class PageTests(unittest.TestCase):
	def test_shell_escapes_title_and_roundtrips_page_data(self):
		data = {"page": "account", "profile": {"full_name": "</script><script>alert(1)</script>"}}

		def render(template, context, **kwargs):
			self.assertFalse(kwargs["is_path"])
			return Environment().from_string(template).render(context)

		with (
			patch.object(ui, "assets", return_value={"script": "/ui.js", "style": "/ui.css"}),
			patch.object(ui.frappe, "render_template", side_effect=render),
		):
			html = ui.render_page("passport", "<title>", data)
		self.assertEqual(page_data(html), data)
		self.assertIn("<title>&lt;title&gt;</title>", html)
		self.assertNotIn("<script>alert(1)</script>", html)
