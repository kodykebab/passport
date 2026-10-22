app_name = "passport_client"
app_title = "Frappe ID Client"
app_publisher = "Frappe"
app_description = "Sign in with Frappe ID"
app_email = "developers@frappe.io"
app_license = "mit"
after_migrate = ["passport_client.store.install"]
before_authentication = ["passport_client.federation.boundary"]
protocol_response = ["passport_client.auth.response"]
before_native_session_insert = ["passport_client.federation.insert"]
before_native_session_delete = ["passport_client.federation.deleted"]
validate_native_session = ["passport_client.federation.validate"]
realtime_session_deadline = ["passport_client.federation.socket_context"]
update_website_context = ["passport_client.auth.update_login_context"]
app_include_js = ["/assets/passport_client/js/passport_desk.js"]
doc_events = {"User": {"on_update": "passport_client.federation.user_changed"}}

extend_bootinfo = ["passport_client.auth.extend_bootinfo"]
