# Frappe ID — Passport development PoC

Code and repository naming remain `passport`. See the [active scope](docs/architecture-and-plan.md) and [login/lease contract](docs/prebuild/login-and-lease-contract.md).

Keep the existing bench and Frappe development commit `4dc04fac4dd5bf20feb9f24e029202b3eadb52b8`. This iteration targets `id.localhost:8080` and `site1.localhost:8080`; the existing second site remains outside validation scope. Do not create a fresh bench or upgrade to v16.

Run the existing services with `env/start.sh`. Demo issuer login remains `alice@example.com` / `alice-passport-1`; admin password remains `admin`. Preserve seeded accounts and manually provision identity links and keys. Site users, roles, names and admission remain site-owned.

Implementation sequence: Ed25519 issuer/client authentication and discovery/JWKS; durable native-session-bound five-minute leases across HTTP/realtime; small account page with display-name edits, read-only email and browser-session logout. This is a development PoC, not production readiness.

Email codes, signup, automatic site-user creation, consent/linking screens, email changes/deletion, Cloud integration, notifications and clone/restore orchestration are deferred.

## Run and validate this iteration

Start services with `env/start.sh`. In another terminal, run `env/setup.sh` to migrate **only the existing** issuer and first client, preserve existing keys on reruns, and register the client's public key. It installs the shared `passport_core` helpers into the existing bench and does not create sites or accounts. The retained checkout has the small [framework patch](patches/frappe-development-session-integration.patch) applied; do not replace it with the historical v16 patch.

Open `http://site1.localhost:8080/passport/login`. Manage the issuer profile and browser sessions at `http://id.localhost:8080/account`. After lease expiry, use `/passport/status` on the client to explicitly validate and then return to the site. Interrupted actions are never replayed.

```sh
.bench/env/bin/python -m unittest discover -s tests -p 'test_*.py'
.bench/env/bin/python tests/login_integrity.py
.bench/env/bin/python tests/iteration_e2e.py
.bench/env/bin/python tests/client_auth_integration.py
.bench/env/bin/python tests/account_integration.py
.bench/env/bin/python tests/acceptance_regression.py
.bench/env/bin/python tests/lease_integration.py
.bench/env/bin/python tests/realtime_integration.py
.bench/env/bin/python tests/review_concurrency.py
.bench/env/bin/python tests/review_recovery.py
/opt/homebrew/opt/node@26/bin/node tests/desk_renewal.cjs
```

For code checks, run `uv tool run ruff check apps tests env` and `uv tool run ruff format apps tests env --check`. Ruff runs outside the bench; it does not change its dependencies.

The live tests use dedicated browser sessions of the seeded Alice identity. The account test logs out Alice's issuer sessions; the lease and recovery tests temporarily disable and restore Alice on site1 and the issuer. Run them as development tests, not against users' sessions.

This repository contains source, contracts and test code. Local environment state, test reports, screenshots and historical investigation artifacts are not published. Setup expects the existing development bench and seeded sites; it is not a fresh-install bootstrap.
