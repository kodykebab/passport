# Frappe ID development iteration

Active scope, 20 October 2026. Product branding is **Frappe ID**; repository, apps and identifiers remain `passport`. This development PoC is not a production release.

## Baseline and scope

Keep the existing Mac bench and Frappe development checkout at `4dc04fac4dd5bf20feb9f24e029202b3eadb52b8`. A separately provisioned Ubuntu development bench uses the same checkout and framework patch. Do not upgrade to v16 or replace the Mac bench. Inspect and test native HTTP/session/realtime integration on this checkout.

Implement iteratively against `id.localhost` and `site1.localhost`. Keep the existing demo password authentication, seeded accounts, manual provisioning, identity links and site-owned admission. Leave the existing second site intact; broader multi-site validation is deferred.

1. Replace HS256 with Ed25519 issuer signatures, discovery and JWKS. Authenticate back-channel requests using manually provisioned per-site Ed25519 keys. Preserve code/PKCE, browser state and nonce, exact callbacks, and ID-token-only responses.
2. Add durable federation state bound to native Frappe sessions. Enforce leases of at most 300 seconds on HTTP and realtime. Renew through authenticated server requests. Expired leases block access; only authoritative validation can resume it. Preserve idle and absolute deadlines; renewal is not activity. Local logout and user disablement defeat delayed responses. Never replay interrupted application writes.
3. Add a small account page with editable display name, read-only email, issuer browser sessions, device logout and logout everywhere. Revocation prevents renewal of associated federated sessions; existing leases bound remote access. Require fresh password authentication for sensitive actions. Never overwrite site-owned names.

## Acceptance

Test real login and existing identity links; algorithm/key/audience/nonce and client replay rejection; HTTP and socket expiry; renewal outage/recovery; idle and absolute expiry through cache and SQL; logout/disablement races; device/everywhere revocation; CSRF and password freshness. Record observed results and limitations without treating contracts as implementation evidence.

## Later work

Later work includes automatic site-account creation, remembered/general consent, email change, Cloud directory/provisioning, notifications, clone/restore orchestration and wider multi-site validation. A framework upgrade or replacement bench is outside this iteration.

## Email-login iteration

Add email-code login for existing issuer accounts according to the [email-login contract](prebuild/email-login-contract.md). Retain password login, password confirmation for account mutations, existing identity links and site-owned admission. Use local email capture during development.

## Issuer-signup iteration

Add opt-in email-verified signup for issuer-only identities under the [signup contract](prebuild/signup-contract.md). Preserve native registration restrictions, existing users, password confirmation, manual client links and site admission. No automatic client provisioning.

## Existing-account linking iteration

Add opt-in linking of an existing enabled site account after fresh site-password verification, with no separate administrator approval. Require explicit issuer confirmation of the chosen identity and registered site. Keep local names, roles, accounts and existing links unchanged; do not match by email. The [linking contract](prebuild/account-linking-contract.md) defines session binding, replay/conflict handling and the distinct identity proof. No native framework/UI changes.

## Connected-site management iteration

Add issuer account controls for known authorized sites under the [connected-sites contract](prebuild/connected-sites-contract.md). Fresh-password revocation blocks new grants and renewals; allowing future sign-ins never revives old requests or sessions. Preserve site accounts, identity links and admission. Deployment clearance remains separate and pending.

## Account deletion and site-picker iterations

Implement issuer self-deletion under the [deletion contract](prebuild/account-deletion-contract.md), followed by authenticated site navigation under the [picker contract](prebuild/site-picker-contract.md). Preserve site-owned accounts/data and admission; email changes remain deferred. Production and broader validation follow these features separately.
