# Framework candidate patches

Use the patch directory for the exact framework revision:

| Framework | Revision | Directory |
| --- | --- | --- |
| v16.54.0 | `2fcb9397a1f1da353b21e53d2f97ec56653acd02` | This directory |
| v17 development | `22053d4a3a14c08dad29776a7b58422710ad409a` | `v17/` |

Apply these patches in order from that directory to an isolated checkout:

1. `frappe-development-session-integration.patch`
2. `frappe-telemetry-privacy.patch`

The first patch supplies the native session and protocol boundaries used by the Passport client and enforces lease expiry on both Node and Python realtime backends. Python uses its existing socket Session for deadlines and revalidation; no additional service or persisted membership registry is needed. The second protects the tested payload copies in error/message and sampled-transaction telemetry and omits automatic traceback locals and request/job payload metadata from native Error Log. Both are required for this candidate. The privacy patch preserves non-database span details, exception messages, routes and job identifiers; applications must not embed credentials in those retained fields.

Run `git apply --check` before each patch. Do not apply a patch over unknown local changes or assume compatibility with another Frappe revision. The release packaging check applies both to a clean export and compares the result with the tested framework candidate.

v17 stores native session IDs as hashes. Its deletion hook passes the stored identifier with `stored=True`; validation hooks still receive the raw request identifier. Passport uses the framework's hash function for native lookups and stored session references. The existing Passport install/migration hook converts older references idempotently. v16 keeps its existing raw-ID storage. These representations stay inside each site; the signed cross-service protocol is unchanged.

This is a development candidate. Enrollment, Pilot lifecycle fencing and ordered picker reporting have isolated backend acceptance tests. Browser verification and deployment-specific operational checks remain release gates. Do not use the development setup scripts against production or treat the archive as a deployment approval. Preserve each environment's existing settings and secrets.
