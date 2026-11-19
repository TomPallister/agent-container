# Security model

This project is a development environment, not a production multi-tenant sandbox.
The host operator, Podman engine, installed secret-proxy, selected image and this
launcher are trusted. User configuration can execute code as the developer.

## Intended boundaries

- The development VM has no host filesystem shares. The development container
  mounts only its named encrypted-data volume, not host home, SSH sockets or a
  host container-engine API.
- The development network is internal. A separate transport relay forwards
  HTTP(S) proxy connections to one configured host-loopback proxy port. Direct
  external egress is blocked; there is no automatic direct fallback.
- Host and container verify the proxy's TLS certificate and exact current boot
  nonce before receiving command environments or unlocking the workspace.
- API credentials remain in secret-proxy. Container clients receive references,
  dummy AWS selectors, and/or sealed auth records. The proxy capability itself
  IS accessible to the development user and its processes: it authorises proxy
  use, not vault extraction.
- Passphrases go to gocryptfs on stdin, not command arguments or container
  configuration. Optional host 1Password reads use an explicit account and do
  not inherit a service-account token.
- Proxy capabilities travel in a temporary owner-only env file at exec time;
  they are not baked into images or stored in container create-time environment.
- Port relays accept only explicit nested TCP publications, bind host loopback,
  reject privileged/reserved ports and do not expose a host engine socket.

## Limits and accepted trade-offs

A running agent can read/write the unlocked workspace, execute code, use all
access granted by the selected proxy policy and upload data to reachable
destinations. This is not a per-agent permissions boundary or a data-loss
prevention system. Do not grant integrations an agent should not use.

The operator can access the development engine and live plaintext. Encryption
protects the locked named volume, not a running session against its host owner.
Only content under ~/dev is covered: tools can still write caches elsewhere in
the writable container layer. Keep valuable/sensitive state in the workspace.

FUSE and nested rootless containers require SYS_ADMIN, SYS_CHROOT, SETUID,
SETGID, CHOWN, DAC_OVERRIDE and FOWNER in the outer rootless user namespace.
No-new-privileges is enabled, but SELinux container labelling and AppArmor
confinement are relaxed for compatibility, and proc masking is relaxed for
inner PID namespaces. These are real containment trade-offs. Kernel/runtime
vulnerabilities can defeat these boundaries.

The watchdog is convenience, not a security timer an attacker cannot evade.
Open terminals defer locking; busy mounts are not lazily detached and falsely
reported as locked. Stop the container when access should end.

Do not introduce OAuth token exchanges inside the container merely because
the proxy can inject a client secret. Their responses can vend real reusable
credentials. Use proxy-owned derived tokens or explicitly supported sealed auth.
The environment cannot prevent every credential-returning API; proxy policy is
responsible for the services it exposes.

Pinned base digest, tool checksums and npm lockfile improve provenance, not
proof of vulnerability freedom. Ubuntu packages use mutable repositories.
The preview has functional isolation tests, not an independent penetration test
or a complete vulnerability clearance. Do not present it as such.

## Explicit file transfers

`copy`/`move` intentionally cross the host/workspace data boundary at caller-chosen
paths. They do not mount host directories or vend credentials. Receivers validate
a bounded typed stream and use no-follow descriptors and atomic no-replace
publication, not general archive extraction. Exports are plaintext on the host;
content can still be malicious. Moves require quiescent source writers and trust
the selected engine/image; they do not prove durable storage against a compromised
destination. See [transfer limits and recovery](docs/transfers.md).

## Reporting

Use GitHub private vulnerability reporting for this repository. Do not post
credentials, decrypted workspace content, proxy capabilities or private policy
in a public issue. Report engine/injection issues to secret-proxy instead.
