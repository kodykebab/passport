# Login and renewable lease contract — current iteration

Keep existing code/PKCE routes and ID-token-only responses. Use EdDSA restricted to Ed25519, purpose-specific token types and key IDs. Publish discovery and issuer public JWKS. Never trust token-supplied key URLs.

Manually provision a separate site private key and issuer-held public key. Bind short-lived client assertions to the exact endpoint and request body; reject replay. Keep demo password login (`amr=["pwd"]`), seeded users and existing identity links. No automatic site-user creation or email linking.

Durable issuer login records bind subject, client and issuer native browser session. Durable client federation records bind protocol login to native session and immutable identity. A lease expires no later than issuance + 300 seconds or either absolute deadline. Renewal validates authoritative issuer session/account and local native session/user/identity. Renewal never changes last activity or authentication time. Local logout and disablement cannot be undone by delayed responses. Missing federation context fails closed.

Lease expiry blocks protected HTTP and realtime without deleting an otherwise-live native session. Explicit renewal can resume access only after authoritative validation; outage time does not pause idle or absolute expiry. Never automatically replay interrupted writes. Account device/everywhere logout revokes issuer sessions and associated login authority; remote access ends within the current lease bound.

Use native CSRF protection and fresh password verification for sensitive account mutations. Email is read-only. Display-name updates affect issuer profile only.

Cloud generations, consent, recovery epochs, notification delivery, automatic provisioning and broader lifecycle orchestration remain later work. Framework assumptions must be verified on `4dc04fac4dd5bf20feb9f24e029202b3eadb52b8`.
