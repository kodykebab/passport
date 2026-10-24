"""Read server-provided page data for HTTP protocol tests, not browser acceptance."""

import json
import re


def page_data(html):
	return json.loads(
		re.search(r'<script id="passport-data" type="application/json">(.*?)</script>', html, re.S)[1]
	)
