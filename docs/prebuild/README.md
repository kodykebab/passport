# Development contracts

The [active iteration](../architecture-and-plan.md) defines this PoC. Retain the existing Frappe checkout at `4dc04fac4dd5bf20feb9f24e029202b3eadb52b8` and existing bench. Validate one issuer and one client, with manual keys and demo password authentication.

The [login and lease contract](login-and-lease-contract.md) governs this slice. Cloud provisioning, a framework upgrade and broader release designs are deferred.

The [email-login contract](email-login-contract.md) adds code login for existing issuer users. Account actions retain fresh-password confirmation.

The [signup contract](signup-contract.md) adds opt-in verified issuer registration without client-site provisioning.

The [account-linking contract](account-linking-contract.md) adds opt-in linking of existing site accounts after fresh site-password verification and explicit issuer consent.

The [connected-sites contract](connected-sites-contract.md) adds issuer-side site access revocation without deleting site accounts or identity links.

The [deletion contract](account-deletion-contract.md) defines durable issuer removal without site data deletion. The [picker contract](site-picker-contract.md) defines navigation through existing site login and admission.
