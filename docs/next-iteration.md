# Deployment-readiness scope

Approved scope: make the existing one-issuer/one-client development PoC repeatable to deploy and validate. Keep the retained Frappe commit, native Desk UI, seeded accounts, manual identity/key provisioning and existing authentication contracts. This is not a production-release declaration.

## 1. Preserve authentication limits across operational changes

Define and verify the expected lifetime of email issuance counters across web-worker restarts, source synchronization, asset builds and supported cache clearing. Use isolated test keys and controlled operations to identify any loss without resetting live counters or sending extra mail. Do not change the configured five-per-email and twenty-per-IP limits. Redis outage must continue to fail closed. Distinguish intentional expiry from unexplained loss; unexplained loss blocks sign-off.

## 2. Make deployment repeatable

Provide provider-neutral build/deployment checks for the existing bench. Verify installed app paths, frontend dependency/build prerequisites, generated asset manifests, background workers, required hooks and native setup completion. Preserve existing configuration and refuse to silently create users, grant roles, mark setup complete or rotate keys. Retain old fingerprinted assets while existing pages may still request them. Avoid broad cache clearing as a deployment shortcut.

Keep Mailpit and machine-specific service configuration outside the published deployment path. Document the issuer's native outgoing Email Account or delivery-service configuration, queue ownership and failure diagnostics. Do not introduce a Passport-specific mail provider.

## 3. Complete bounded acceptance

- Repeat email/password login, account navigation, explicit lease recovery and logout on the exact deployed revision, within an uninterrupted normal rate window.
- Validate isolated issuer devices and their associated client sessions; selected-device revocation must leave the other device usable. Do not revoke unrelated sessions.
- Exercise actual hidden-tab behavior only when the browser reports it as hidden; retain navigation-away evidence as a separate case.
- Test a committed application write with a lost response using an action the seeded user already has permission to perform. Do not grant roles or bypass authorization to construct the test.
- Retain existing expiry, idle/absolute timeout, revocation-race and no-write-replay regressions.

## Completion boundary

Document reproducible setup and smoke commands, with test outcomes and screenshots kept local. Report each acceptance case as PASS, FAIL or BLOCKED and state the tested boundary. Resolve unexplained rate-limit loss before calling deployment readiness complete. No framework upgrade, replacement bench, native UI modification, new account-management feature or Cloud provisioning integration belongs to this operational slice. Preserve the existing opt-in signup and account-linking settings.
