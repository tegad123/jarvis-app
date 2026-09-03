# Shape 1 — Connect Google as the assistant account

The tenant's agent is a dedicated Workspace user (`identity.assistant_address`),
named `{agent_display_name}`. Not an alias on the owner.

1. Client creates the Workspace user. That address **is** `assistant_address`.
2. MFA on that account per the client's policy.
3. Add that address to the OAuth client's test users during verification.
4. Owner or admin opens Connect Google **signed in as the assistant account**
   (browser Google session is `{agent_name}@{clientdomain}`).
5. After connect: `google_connection.google_email` MUST equal
   `identity.assistant_address`. A mismatch announces (log + counter) and is a
   failed verify.

`send_as_alias` is rejected.
