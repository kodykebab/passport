# Issuer account deletion

Self-service deletion requires an enabled, non-standard issuer user with a Passport Account, a valid native session, explicit CSRF, fresh password confirmation and the exact confirmation DELETE. System Managers must use operator-managed removal; this endpoint never removes issuer operators. Client site accounts, identity links, names, roles and business data remain untouched.

Persist a deletion intent and disable the issuer User under its existing lock before native deletion begins. Advance its authentication generation. Pending deletion blocks native session creation even if the User is accidentally re-enabled. Native session deletion commits internally in the retained framework, so revocation must already be durable. Stop new sign-ins, exchanges and renewals; existing leases retain their original maximum five-minute deadline.

Remove issuer sessions, owned protocol records, Passport Account and native User through supported APIs without bypassing link checks. Native dependency failures leave the account disabled and deletion pending for operator review/retry. Hourly retries are idempotent. Return an honest pending result if physical removal has not completed. Keep only a subject tombstone after successful deletion, preventing subject reuse. A later signup with the same email gets a new subject and no inherited site access.

This deletes the issuer login/profile, not all historical operational records. Native logs, mail records and backups follow their retention policies. Do not promise complete erasure or delete site-owned data. Preserve the native framework/UI; custom controls use official frappe-ui.

Test only disposable accounts: confirmation/CSRF/password/privilege rejection, concurrent revocation, partial native-deletion failure, retry, native-session rejection while pending, no revival after email reuse, and preservation of client accounts/links. Never delete demo accounts.
