# Frappe ID development iteration

Active scope, 20 October 2026. Product branding is **Frappe ID**; repository, apps and identifiers remain `passport`. This development PoC is not a production release.

## Baseline and scope

Keep the existing bench and Frappe development checkout at `4dc04fac4dd5bf20feb9f24e029202b3eadb52b8`. Do not upgrade to v16 or create a replacement bench. Inspect and test native HTTP/session/realtime integration on this checkout.

Implement iteratively against `id.localhost` and `site1.localhost`. Keep the existing demo password authentication, seeded accounts, manual provisioning, identity links and site-owned admission. Leave the existing second site intact; broader multi-site validation is deferred.

1. Replace HS256 with Ed25519 issuer signatures, discovery and JWKS. Authenticate back-channel requests using manually provisioned per-site Ed25519 keys. Preserve code/PKCE, browser state and nonce, exact callbacks, and ID-token-only responses.
2. Add durable federation state bound to native Frappe sessions. Enforce leases of at most 300 seconds on HTTP and realtime. Renew through authenticated server requests. Expired leases block access; only authoritative validation can resume it. Preserve idle and absolute deadlines; renewal is not activity. Local logout and user disablement defeat delayed responses. Never replay interrupted application writes.
3. Add a small account page with editable display name, read-only email, issuer browser sessions, device logout and logout everywhere. Revocation prevents renewal of associated federated sessions; existing leases bound remote access. Require fresh password authentication for sensitive actions. Never overwrite site-owned names.

## Acceptance

Test real login and existing identity links; algorithm/key/audience/nonce and client replay rejection; HTTP and socket expiry; renewal outage/recovery; idle and absolute expiry through cache and SQL; logout/disablement races; device/everywhere revocation; CSRF and password freshness. Record observed results and limitations without treating contracts as implementation evidence.

## Later work

Later work includes email codes, public signup, automatic site-account creation, consent/linking screens, email change, account deletion, Cloud directory/provisioning, notifications, clone/restore orchestration and wider multi-site validation. A framework upgrade or replacement bench is outside this iteration.
