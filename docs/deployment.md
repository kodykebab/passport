# Deploy the existing development PoC

This procedure updates a Passport installation on either exact Frappe revision listed with the [framework candidate patches](../patches/README.md). It does not create a bench, site, account, identity link or key. The preflight selects patches from the checkout's commit and rejects an unknown revision or either missing patch. Native Desk supplies invitation administration through standard DocType scripts.

Prepare a separate bench for this candidate. Do not downgrade an existing v17 development database to v16. Migrating an existing application deployment requires its own supported version path and database compatibility checks. Both Node and Python realtime backends enforce the same Passport lease boundary. The pinned revisions still require dependency-advisory review; passing preflight is not production approval.

When upgrading from raw session storage to v17, stop web, worker and scheduler processes during migration. Run the normal framework migration and Passport migration before starting all processes on the matching source. Passport converts federation keys and local/issuer session references to native hashes. Do not mix old and new writers during this conversion or roll the code back onto the migrated database. Restore the reviewed pre-upgrade database and source together if rollback is required. A fresh v17 installation does not prove the upgrade path of an existing application deployment.

## Check before changing anything

From the Passport checkout, using its existing bench Python:

```sh
.bench/env/bin/python env/deployment.py check
```

The check verifies source installation paths, the retained framework commit/patch, build tools and compatible Node version, site configuration, Passport schema, email-limit persistence hook, outgoing mail selection, a registered default-queue worker, native client setup completion and custom UI manifests. It reports missing prerequisites without provisioning them or printing secret values. A registered worker and configured mail account do not prove successful delivery; verify that separately through the normal login flow.

Use `--bench`, `--issuer` and `--client` to select the existing installation. Defaults are `.bench`, `id.localhost` and `site1.localhost`. This is still a one-issuer/one-client procedure. Both pinned Frappe revisions require Node 24 or newer; the retained Ubuntu environment uses Node 24.

If client setup is incomplete, complete the standard Frappe setup lifecycle as Administrator. Do not grant setup roles to federated users or directly toggle setup flags. Missing signing keys, identity links or email-challenge keys require the existing explicit manual provisioning process; a deployment never rotates or creates them silently.

## Production prerequisites

The development `env/start.sh` process group is not a production service manager: an exiting child stops the group. Run web, realtime, workers and scheduler under the deployment's process manager with independent restart policies, startup ordering and health monitoring. Keep database and Redis lifecycle separate from application releases.

Email issuance limits live in cache Redis. The native persistent-cache hook does not provide disk persistence. Require a reviewed persistence and capacity policy for that instance, and verify recovery retains unexpired counters. Eviction can remove limits even when persistence is enabled; account for this when choosing the memory policy and monitoring capacity. Do not change a shared Redis instance's policy without assessing its other users. After cache data loss, keep email-code issuance unavailable for the one-hour rate-limit window rather than treating the reset as a new allowance.

Configure HTTPS origins consistently across native `host_name`, issuer URL, registered client callback and backchannel endpoints. Terminate TLS only at the intended ingress, restrict direct backend access and overwrite forwarded scheme/client-address headers there. Verify trusted certificates, secure cookies, redirects, the complete code/PKCE flow and realtime WebSocket upgrades through that ingress. A direct TLS backend check cannot establish reverse-proxy correctness.

Use the actual outgoing mail service and an authorized recipient to verify delivery. Development mail capture does not establish provider authentication, sender-domain configuration or inbox delivery. Keep public signup and account linking at their existing settings until explicitly enabled by the operator.

## Update and build

Preserve local changes and back up the database/configuration using the environment's existing operations procedure. Confirm the intended Git revision, then fast-forward the source checkout. Use a maintenance window: source reloads and the native bundle's cleanup step can briefly expose incomplete assets while a build is running. The command shares the native `bench_build` filesystem lock, covering dependency installation through final validation. Another build must finish before it can proceed. It is not an atomic release/deployment system.

```sh
.bench/env/bin/python env/deployment.py build
```

This installs the pinned standalone frontend dependencies, builds the public UI and runs Frappe's existing asset bundler for the client integration. It preserves older custom UI hashes and restores older native client-bundle hashes after a successful build. If compilation, cache refresh or final validation raises an error, it restores both apps' prior UI assets, the client bundle directory and both native asset manifests, then invalidates the asset-manifest cache. Failed first builds remove newly created assets instead. Only the asset-manifest cache is invalidated. Old assets are not garbage-collected automatically; retain them through the active-session/loading window and prune during a separate maintenance operation.

**Do not use `bench build` against the live retained environment for this update.** In this checkout, `frappe.build.bundle` calls Redis `FLUSHDB` after bundling. That removes issuance counters across sites, bypassing `persistent_cache_keys`. The Passport command deliberately invokes the same asset bundler without this destructive final cache step. It does not patch Frappe or override its command. Redis flushes, eviction or loss of an ephemeral Redis dataset still lose Redis-backed counters; the persistence hook protects supported site cache clearing, not arbitrary data loss.

Restart web, workers and scheduler through the existing process manager so they load the updated Python source. Do not restart or flush Redis as a code-deployment shortcut. This command does not manage services or run schema migrations. When an update requires a migration, review that update's explicit migration instructions before running it. Re-run the check and verify the expected revision before reopening traffic.

## Signup and linking updates

Deploying source does not enable public signup or account linking. Preserve existing `passport_allow_signup` and `passport_allow_linking` settings. On a site enabling signup, native Frappe signup must remain disabled; password-verified linking operates only on existing site users. Neither feature provisions site admission or replaces existing links.

Linking registers `passport.consent.cleanup`; account deletion registers `passport.deletion.retry_pending`. Both are hourly issuer jobs and do not change DocType schemas. During the maintenance window, synchronize scheduler definitions through the native API from the existing bench:

```sh
bench --site id.localhost execute frappe.core.doctype.scheduled_job_type.scheduled_job_type.sync_jobs
```

Use the actual issuer site name if it differs. This registers jobs from installed-app hooks; it does not run them, flush Redis or enable a disabled scheduler. Restart the existing scheduler/worker processes afterward and verify that both hourly jobs are registered. A standard reviewed migration also synchronizes these definitions. Avoid the development provisioning script for routine source-only updates: it intentionally provisions the development protocol configuration.

## Client registration approval

Passport System Managers control the existing Passport Client record. The internal `passport.setup.register_client` helper requires that role and uses native document permissions. Do not expose the helper as an unauthenticated provisioning endpoint.

Changes to the registered public key, home URL, callback or enabled state rotate a hidden registration revision. Pending consent, authorization grants and login records retain their original revision. Token exchange and lease renewal recheck the current registration under a database lock before proceeding. Disabling and re-enabling a registration does not restore old grants. Title edits preserve the revision. Existing records without a revision remain compatible until a trust field changes; migrate the issuer schema before deploying this code.

A registration change prevents future renewal. A lease already issued can remain usable until its existing expiry, at most five minutes. The client binds each new federated session to the site's `passport_installation` configuration value. A changed marker rejects old sessions even after `passport_disabled` is cleared. Independent native sessions retain their existing behavior. Legacy sites without a marker remain compatible until the first installation change.

Pilot suspends existing Passport configuration and changes this marker before database/configuration restore or reinstall. It excludes Passport configuration from imported backups. Use Pilot's `passport-prepare`, `passport-review`, and `passport-confirm` commands for enrollment. Preparation returns a public request. A Passport System Manager verifies the destination and calls `passport.setup.approve_enrollment(request, title)` in the issuer administrator console. Confirmation verifies a short-lived signed response and activates only the explicitly reviewed local identity associations. The private signing key stays in the destination configuration. No issuer administrator credential is given to Pilot. Clones require a distinct client ID and destination origin. Before ownership transfer, use `passport-suspend` and repeat approval and local admission review.

The client's existing scheduler delivers pending site-access outcomes from SQL to `/passport/site-access`. Enable the site scheduler for picker updates. Reports are authenticated by the site's approved key and ordered by installation and sequence; delayed reports cannot undo a newer denial. Confirmed denial removes the site from the picker. Transport failure retains the pending report and does not invent a denial. Reports do not change the user's separate Passport consent. Registration replacement hides prior-installation outcomes until the new installation confirms access. The picker is discovery, not an authorization boundary: sites still check admission and native permissions when a user enters.

## Operational checks

Before enabling a destination, verify that its native scheduler and a default-queue worker run, the approved installation matches its admission policy, and no enrollment remains pending. Use `bench --site SITE doctor` to inspect scheduler/workers and the native Scheduled Job Type for `passport_client.discovery.deliver` to inspect delivery failures. Enabling the scheduler is an explicit operator action; a successful asset build does not enable it. Keep a failed or interrupted enrollment suspended and repeat the documented review/confirmation workflow.

Run `bench --site SITE execute passport_client.discovery.get_status` as an administrator to see current-installation pending/retrying counts, suspension and scheduler configuration without printing keys or account identifiers. `scheduler_enabled` reports configuration, not process health; use `doctor` for the worker check. A nonzero retry count means delivery has failed and remains pending. Check issuer reachability and the current approved registration, then let the existing scheduler retry. Native Scheduled Job Type can enable execution logs when diagnosing an unexpected worker error.

The privacy patch omits request payloads and automatic traceback locals from telemetry, native Error Log and failed Scheduled Job Log tracebacks. It retains exception type, message and stack locations, plus request method/path or job identity. Explicit messages supplied by callers must not contain credentials. Normal Pilot access logs omit query strings, but nginx upstream errors can include full invitation URLs. Redirecting after receipt does not prevent that exposure.

Before enabling production invitations, verify that proxy error logs, rotated files, collector buffers, exports and backups are readable only by trusted infrastructure operators and required service accounts. Check Pilot log viewing and download access. Fluent Bit forwards nginx error logs, so its destination needs the same controls. Configure finite retention and verify deletion there and at external gateways. Record these checks in deployment approval. Pilot does not automatically enforce private log permissions or retention. Do not copy raw logs into support tickets.

## Native email service

Passport calls `frappe.sendmail`; Frappe Email Queue and the existing mail service own transport/retries. Configure the issuer's outgoing Email Account with the provider's SMTP settings, or its installed native `override_email_send` delivery integration. Use the sender/domain required by that service. Provider credentials stay in the site's native configuration, never the repository. Passport does not select or require SendGrid or another provider.

`passport_email_sender` is optional. If set, the native outgoing-account resolver must be able to serve that sender. Otherwise Frappe selects its configured outgoing account. Ensure a default-queue worker and scheduler are running, and inspect Email Queue status using authorized native administration. Do not expose codes or credential-bearing queue diagnostics in reports. An accepted queue entry is not proof of delivery.

For local capture only, use a loopback Mailpit SMTP endpoint and inbox. It is not a deployment provider. Do not run local provisioning helpers against an externally configured issuer.

## Smoke and recovery

Use dedicated sessions and the unchanged seeded identity:

1. Password login from the client returns to the native user landing page. Check Website Users as well as System Users; Passport must not grant Desk permissions to change the destination.
2. Email-code login delivers through the configured native service and returns to the native landing page. Respect five sends per email and twenty per IP per hour; never clear counters to make a test pass.
3. Account email remains read-only, display name remains unchanged, and sensitive actions still require a fresh password.
4. Leave the application for more than five minutes, confirm access blocks, then explicitly validate and return through the native landing page. Never replay the interrupted application action.
5. Local logout ends access; issuer device logout prevents later client renewal. On failed renewal, verify that retry remains available and that “Log out and return to login” ends the local session through native CSRF-protected logout. Test fresh local login during issuer outage and after issuer revocation. Clean up only the dedicated sessions.

Record actual outcomes and screenshots locally. A build/preflight success does not establish delivery, hidden-tab behavior, every network failure or production readiness. If a build or check fails, keep the maintenance window open and investigate. Automatic build rollback restores assets, not Python source, dependency installations, database migrations or service configuration. A hard process kill, host failure or storage failure can prevent rollback; use the preserved source/assets and operational backups for those cases. Do not reset identity/session state as a recovery shortcut.
