# Connected-site management

The issuer account page lists sites authorized through this issuer, including legacy login records. It is not an inventory of every site-side identity link. Client titles and origins come from the existing registered clients; missing registrations are shown without a visit link.

Revoke access and Allow future sign-ins require explicit native CSRF, a valid issuer session and fresh password confirmation with existing throttling. Revocation persists a per-user/per-client generation and blocks new authorization, code exchange, completion and renewal. Every grant and login binds that generation. Existing records without a generation mean generation zero. Re-enabling permits only new requests; it never revives old grants, consent requests or sessions. Revocation is idempotent while already revoked.

All protocol checks and account mutations serialize on the issuer User row before native session and protocol state, using current reads for generation checks. A response issued before revocation may retain its original lease deadline, at most five minutes from issuance; it cannot authorize a later renewal. No interrupted application writes are replayed.

Site accounts, identity links, roles, names, local password sessions and admission settings are untouched. No backchannel deletion, automatic site provisioning, global logout or native UI changes are introduced. The page uses existing official frappe-ui controls and fresh-password forms, with clear revoked status and five-minute expiry wording.

Validate CSRF/password failures, client/user isolation, old and pending grants, initial completion, renewal and revocation races, repeated revoke and re-enable without resurrection. Use dedicated fixtures; keep results local and public signup/linking settings unchanged.
