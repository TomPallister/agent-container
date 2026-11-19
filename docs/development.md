# Development and evidence

Use an isolated worktree. Run ./test.sh (uv-managed Python, stdlib only), bash -n
and ShellCheck for changed shell files. No GitHub Actions.

## Disposable live smoke

Build the image, then run from a normal host terminal:

```bash
uv run --no-project python tests/smoke_macos.py \
  --machine agent-container \
  --proxy-config /private/etc/secret-proxy/YOUR-INSTANCE/client.json
```

The machine must already exist, use libkrun and have no host shares. The script
creates uniquely named test containers/networks/volume, uses a synthetic
passphrase, and removes only its owned resources. It never restarts the proxy.

Optional --github-secret NAME, --signing-key-file public.key and
--model provider/model enable read-only GitHub, signing and one tiny billed model
request. Do not use raw API keys. Authentication output is not printed.

Acceptance covers real TLS/boot nonce and wrong-auth rejection, external egress
denial, encrypted init/mount/unmount, bundled tools, nested build/run, loopback
publication, loss of proxy transport and persistence across recreation.

This is an integration test on an existing host, not a claim that fresh
macOS prerequisite installation or every corporate network has been tested.
Linux runs the unit/runtime suites in a disposable container; the Linux host
launcher itself is not supported yet. The source image has arch-aware downloads,
but amd64 execution needs its own acceptance run.

Use tests/mutations.py and tests/transfer_mutations.py to check that selected
negative assertions fail when the implementation is deliberately broken.
Private live-service evidence and full logs must stay outside the public source
tree.

## First-run rehearsal

A separate fresh-consumer rehearsal passed on Apple Silicon on 19 November 2026:
fresh HOME and clone, a newly created libkrun VM with no shares, cold image build,
interactive setup/manual passphrase, bare interactive shell, generic Pi defaults,
sealed Codex import and a real model call, public Git, blocked direct egress,
explicit recreation with data/overrides preserved, copy/move and stop. No existing
agent-container configuration, migrated workspace or private Pi profile was used.

**Boundary:** Podman, Command Line Tools, corporate public CA and a running proxy
were already installed. This does not validate prerequisite installation on a
clean Mac, a second OS user, Intel, Linux/WSL hosts or a colleague's vault/policy.
The live workspace, bots and proxy were checked unchanged.

To repeat, isolate HOME/configuration and use a new, explicitly disposable VM;
never repoint an existing user's HOME or reuse their data. Keep temporary HOME
paths short: macOS Unix-domain sockets have a small path limit. A PTY driver must
preserve a normal interactive shell/job-control boundary, not make the setup CLI
itself the terminal session leader: closing that test session can terminate the
VM. An abrupt VM termination can damage Podman's writable-layer metadata.
Record the synthetic passphrase once and reuse its exact bytes. Never repair or
reinitialise an existing user's store to make a test pass. Remove only resources
whose creation and identity were recorded by that rehearsal.

The first-run unit tests in tests/test_setup.py cover private settings, explicit
helper opt-out, no ambient vault access during configuration, rejected
credentials/untrusted proxy input, existing-file/symlink refusal, and retained
settings after a failed build so recovery uses explicit build/init commands.
