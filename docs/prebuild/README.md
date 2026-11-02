# Development contracts

These documents record the [development iterations](../architecture-and-plan.md). The current candidate uses the pinned framework revisions and administrator-approved enrollment described in [deployment](../deployment.md). Preserve existing benches; validate against separate disposable issuer and client sites.

The [login and lease contract](login-and-lease-contract.md) defines authentication and session boundaries. Deployment documentation governs the current framework and lifecycle integration.

The [email-login contract](email-login-contract.md) adds code login for existing issuer users. Account actions retain fresh-password confirmation.

The [signup contract](signup-contract.md) adds opt-in verified issuer registration without client-site provisioning.

The [account-linking contract](account-linking-contract.md) adds opt-in linking of existing site accounts after fresh site-password verification and explicit issuer consent.

The [connected-sites contract](connected-sites-contract.md) adds issuer-side site access revocation without deleting site accounts or identity links.

The [deletion contract](account-deletion-contract.md) defines durable issuer removal without site data deletion. The [picker contract](site-picker-contract.md) defines navigation through existing site login and admission.
