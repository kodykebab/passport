app_name = "passport"
app_title = "Frappe ID"
app_publisher = "Frappe"
app_description = "One identity across Frappe sites"
app_email = "developers@frappe.io"
app_license = "mit"
after_migrate = ["passport.store.install"]
before_authentication = ["passport.oidc.boundary", "passport.email_login.boundary"]
protocol_response = [
	"passport.oidc.response",
	"passport.account.response",
	"passport.sites.response",
	"passport.email_login.response",
	"passport.login.response",
]
before_native_session_insert = ["passport.account.insert"]
doc_events = {"User": {"on_update": "passport.account.user_changed"}}
website_user_home_page = "account"

validate_native_session = ["passport.account.validate_session"]

scheduler_events = {"hourly": ["passport.email_login.cleanup", "passport.consent.cleanup"]}
persistent_cache_keys = ["passport:email-limit:"]
