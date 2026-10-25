# Frappe ID — Passport development PoC

Code and repository naming remain `passport`. See the [active scope](docs/architecture-and-plan.md) and [login/lease contract](docs/prebuild/login-and-lease-contract.md).

Keep the existing Mac bench and Frappe development commit `4dc04fac4dd5bf20feb9f24e029202b3eadb52b8`. A separate Ubuntu development bench uses the same commit and framework patch. This iteration targets `id.localhost:8080` and `site1.localhost:8080`; the existing second site remains outside validation scope. Do not upgrade to v16 or replace the Mac environment.

Run the existing services with `env/start.sh`. Demo issuer login remains `alice@example.com` / `alice-passport-1`; admin password remains `admin`. Preserve seeded accounts and manually provision identity links and keys. Site users, roles, names and admission remain site-owned.

Implementation sequence: Ed25519 issuer/client authentication and discovery/JWKS; durable native-session-bound five-minute leases across HTTP/realtime; small account page with display-name edits, read-only email and browser-session logout. This is a development PoC, not production readiness.

Email-code login is available for existing issuer accounts alongside password login. Account changes still require a fresh password. Automatic site-user creation, email changes, Cloud integration, notifications and clone/restore orchestration are deferred. Opt-in issuer signup and existing-account linking are described below.

## Run and validate this iteration

Start services with `env/start.sh`. In another terminal, run `env/setup.sh` to migrate **only the existing** issuer and first client, preserve existing keys on reruns, and register the client's public key. It installs the shared `passport_core` helpers into the existing bench and does not create sites or accounts. The retained checkout has the small [framework patch](patches/frappe-development-session-integration.patch) applied; do not replace it with the historical v16 patch.

Open `http://site1.localhost:8080/passport/login`. Manage the issuer profile and browser sessions at `http://id.localhost:8080/account`. After lease expiry, use `/passport/status` on the client to explicitly validate and then return to the site. Interrupted actions are never replayed.

## Custom interfaces

The public password-login, email-code, account and session-recovery pages use official `frappe-ui` 1.0.2 with Vue 3. The standalone frontend build does not change Frappe's dependencies or require a Desk session. The issuer password screen calls native Frappe authentication, preserving its password policy and throttling. Native Desk UI remains framework-owned. The PoC supports the existing password-only accounts; additional native authentication steps are not implemented by this screen and fail closed.

Use the [existing-environment deployment procedure](docs/deployment.md). It checks prerequisites and builds without the retained native `bench build` command's Redis flush:

```sh
.bench/env/bin/python env/deployment.py check
.bench/env/bin/python env/deployment.py build
```

Build before serving custom pages. Both apps receive fingerprinted public assets; older hashes remain available after rebuilding. The Desk notice uses the same components inside a shadow root to isolate its styles. Generated assets are ignored.

Rendered form regressions run with `npm test --prefix frontend` after `npx --prefix frontend playwright install chromium`. They use synthetic page data and do not replace live browser acceptance.

The client must have completed Frappe's normal setup lifecycle before non-admin users enter Desk. Run standard setup as Administrator with the intended regional settings. Do not grant setup permissions to federated users or directly flip setup flags.

## Email login

Passport uses `frappe.sendmail`, the native Email Queue and the issuer's configured email service. Configure an outgoing Email Account or Frappe Cloud's Email Delivery Service on the issuer, then run `bench --site <issuer> execute passport.setup.provision_email_login` from the bench directory. This only creates the challenge digest key if missing; it does not configure a provider or change signing keys. `passport_email_sender` is an optional sender override; otherwise Frappe selects its configured outgoing account.

Delivery runs after the challenge transaction commits. The native queue owns transport, retries and delivery-status records. Passport cleanup removes expired challenges while retaining queue records for late provider callbacks; configure mail retention through Frappe's Log Settings and the provider.

### Local mail capture

For local testing, run Mailpit bound to loopback (`127.0.0.1:1025` for SMTP and `127.0.0.1:8025` for its inbox), with captured mail stored outside version control. Configure a native outgoing Email Account on the issuer to use that SMTP endpoint without TLS or authentication. Use a development-only sender and the provider-neutral provisioning command above. This configuration is only for local capture; deployments use their configured Frappe mail service.

Choose **Use email code** on the issuer login page and retrieve the matching request identifier from the local inbox. Use the same browser to enter the code. Each tab has an independent attempt; resends replace the previous code. Issuance counters use Frappe's persistent-cache hook so ordinary site cache clearing preserves their limits and expiry. The [email-login contract](docs/prebuild/email-login-contract.md) defines expiry, limits and session behavior.

## Regression tests

```sh
.bench/env/bin/python -m unittest discover -s tests -p 'test_*.py'
.bench/env/bin/python tests/email_challenge_integration.py
.bench/env/bin/python tests/signup_integration.py
.bench/env/bin/python tests/email_login_integration.py
.bench/env/bin/python tests/login_integrity.py
.bench/env/bin/python tests/iteration_e2e.py
.bench/env/bin/python tests/client_auth_integration.py
.bench/env/bin/python tests/account_integration.py
.bench/env/bin/python tests/acceptance_regression.py
.bench/env/bin/python tests/lease_integration.py
.bench/env/bin/python tests/realtime_integration.py
.bench/env/bin/python tests/review_concurrency.py
.bench/env/bin/python tests/review_recovery.py
node tests/desk_renewal.cjs
```

For code checks, run `uv tool run ruff check apps tests env` and `uv tool run ruff format apps tests env --check`. Ruff runs outside the bench; it does not change its dependencies.

The live tests use dedicated browser sessions of the seeded Alice identity. The account test logs out Alice's issuer sessions; the lease and recovery tests temporarily disable and restore Alice on site1 and the issuer. Run them as development tests, not against users' sessions.

The email delivery test needs Mailpit and the bench worker running. It consumes normal email/IP request limits, so repeated runs may need to wait for their one-hour window. Challenge tests use isolated records and rate-limit keys; they do not create users or send mail.

On the dedicated Ubuntu environment, `tests/email_delivery_integration.py --mailpit-pid PID` tests actual SMTP failure and recovery. Run it with the bench Python and the PID of this project's `.data/bin/mailpit` process. It deliberately stops and restarts that process, retries queued delivery after Frappe's undo window, checks rejection after controlled code expiry, and leaves Mailpit running. Do not run it during someone else's browser acceptance session.

This repository contains source, contracts and test code. Local environment state, test reports, screenshots and historical investigation artifacts are not published. Setup expects the existing development bench and seeded sites; it is not a fresh-install bootstrap.

## Verified issuer signup

The [issuer-signup iteration](docs/prebuild/signup-contract.md) adds opt-in registration at `/passport/signup`. It creates only a verified issuer identity, never client-site access. `passport_allow_signup` defaults off; native Frappe registration remains disabled. Development mail capture is not proof of real mailbox ownership.

The signup integration test uses dedicated `example.invalid` issuer accounts, overrides eligibility only inside its test process, suppresses mail/background jobs and removes its fixtures. It does not enable the public signup switch or replace mail/browser acceptance. Native Frappe registration must remain disabled when enabling Frappe ID signup; the two entry points are intentionally separate.

## Link an existing site account

Sites may opt into password-verified linking with `passport_allow_linking`; it defaults off. Users open `/passport/link` (or **Link Frappe ID** in the local Desk menu), sign in with their native site account, confirm that site's password, then approve the chosen Frappe ID on the issuer. No administrator approval is needed for this flow. Native MFA or disabled password login prevents linking through this password-only interface.

Linking creates only the identity mapping for an existing enabled site user. It never matches by email, creates site accounts, changes roles/names or replaces existing links. The current local session stays local; Frappe ID can be used on the next login. Cancellation, expired requests and session changes fail closed. See the [account-linking contract](docs/prebuild/account-linking-contract.md). Manually provisioned links and ordinary login remain supported.

## Connected sites

The issuer account page lists sites authorized through this Frappe ID. Fresh password confirmation is required to revoke access or allow future sign-ins. Revocation blocks new sign-ins and renewals; already issued leases expire within five minutes. Allowing future sign-ins never revives old sessions. Site accounts, data and identity links remain unchanged. The issuer does not enumerate unvisited site-side links. See the [connected-sites contract](docs/prebuild/connected-sites-contract.md).

## Account deletion and site navigation

Eligible issuer users can delete their Frappe ID from the account page with a fresh password and explicit DELETE confirmation. The issuer disables access before removal; native dependency failures leave deletion pending for retry. System Managers require operator-managed removal. Site accounts, identity links and data are preserved. Historical logs, mail and backups follow their retention policies. See the [deletion contract](docs/prebuild/account-deletion-contract.md).

`/sites` lists sites known from issuer authorization history and starts each enabled, non-revoked site's normal login flow. It does not provision accounts or guarantee admission. Reach it through **Your sites** on the account page or the app-provided **Switch site** Desk entry. See the [picker contract](docs/prebuild/site-picker-contract.md).
