# Site picker

The issuer exposes /sites to authenticated users, reachable from the account page and the client's existing app-provided Desk menu extension. Show sites known from issuer authorization history, not a Cloud directory or a promise of current site admission.

Only enabled registered clients with non-revoked authorization receive an Open site link. Derive its origin from the validated registered callback, accept only HTTP(S) without credentials, control characters, query or fragment, and start the site's existing /passport/login route. Do not place tokens, identifiers or session cookies in links. Unknown, disabled, malformed and revoked entries remain unavailable with an account-management path.

Preserve native site admission, identity links and code/PKCE login. Visiting the picker is not consent or reactivation. Opening a site may still be denied by that site's current policy. Keep native Desk untouched apart from the existing app menu extension. Use official frappe-ui buttons, inputs and alerts; support keyboard use and narrow screens. Do not add provisioning or cross-site data calls.
