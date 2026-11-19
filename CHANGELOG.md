# Changelog

## v0.2.0 — testing prerelease

Source-only macOS Apple Silicon preview; no published images or binaries.

- Guarded host/workspace copies and verified host-directory moves: new
  destinations only, through the unlocked encrypted filesystem. See
  [transfer limitations](docs/transfers.md), including plaintext exports and
  non-archival metadata handling.
- Verified offline ciphertext import into a new owned volume, retaining the
  original and quarantining incomplete imports. See [migration](docs/migration.md).
- Reference-only integration environment and GitHub owner/Copilot routing;
  tokenless Google and sealed Checkmarx shims, pinned compatibility tools and
  encrypted Pi state. Proxy policy and lifecycle remain separate.
- First-run setup regression coverage and a fresh-consumer/new-VM rehearsal.
  Setup can explicitly skip a suggested GitHub helper; quick-start commands set
  PATH for the current terminal.

Existing installations must update deliberately. Build does not recreate;
recreate stops processes and discards files outside `~/dev`, retaining ciphertext.
The image inputs are unchanged from the preceding migration/transfer commits,
so this onboarding follow-up alone does not require a running workspace restart.

This is not a clean-OS onboarding or broader-platform certification, a new
comprehensive vulnerability audit, or an escape-proof sandbox. See
[validation scope](docs/development.md) and [security limitations](SECURITY.md).
No production proxy, workspace or retained recovery data was changed during
release validation.

## v0.1.1

Normalised copied image permissions and executable-bit source hashing so a
restrictive checkout umask does not break the bundled tools or defaults.

## v0.1.0

Initial source-only preview: encrypted workspace, installed secret-proxy client,
opinionated tools, nested containers and explicit loopback port forwarding.
