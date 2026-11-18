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

Use tests/mutations.py to check that selected negative assertions fail when
the implementation is deliberately broken. Private live-service evidence and
full logs must stay outside the public source tree.
