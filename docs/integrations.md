# Git and coding-agent integration

agent-container consumes an existing secret-proxy installation. It does not
install policy or broaden permissions. Service names and account choices belong
to the user's private proxy configuration, not this project.

## GitHub

The proxy starter provides read-only api.github.com access. That is enough for
`gh api user`, but **not private Git clone/push**. Configure the corresponding
Git HTTPS policy in secret-proxy before onboarding engineers who need it.

For example, add these placements to your existing GITHUB_TOKEN entry, replacing
example-org/example-repo with the actual repository you intend to permit:

```json
{
  "kind": "basic-password",
  "hosts": ["github.com"],
  "username": "x-access-token",
  "methods": ["GET", "POST"],
  "paths": [
    "/example-org/example-repo.git/info/refs",
    "/example-org/example-repo.git/git-upload-pack"
  ]
}
```

This allows the listed read endpoints, not push. For push, deliberately add the
repo's /git-receive-pack endpoint and use a token with the required repository
permissions. GitHub API writes, PR management, LFS and package registries need
their own reviewed placements/scopes. Follow secret-proxy's supported policy
installation/update process; editing a source file is not deployment.

Set agent-container's githubSecret to the matching name (normally GITHUB_TOKEN).
The helper sends an x-access-token username and a secret reference password.
The real value is injected upstream. Use HTTPS remotes, not SSH.

## Commit signing

The proxy must already be configured with its internal-only GIT_SIGNING_SSH_KEY
SSH Key item. Supply its matching Ed25519 public key file during setup, register
that public key as a signing key with your Git provider, and use a verified
author email. No private signing key is copied into agent-container.

If signing is configured and unavailable, commits fail rather than becoming
unsigned. Git hooks are disabled by default for agent-launched commands. This
does not prevent an authorised user/process overriding its own Git settings.

## Pi and model access

Pi is installed with no private profile or default paid model. Configure provider
reference variables in secret-proxy's client environment, or enable its supported
ChatGPT/Codex integration:

1. Host: `secret-proxy login` (browser approval; real tokens remain in the proxy).
2. Host: `agent-container login` (validates and copies only the sealed Codex entry).
3. Container: `pi`, then `/model`.

The import does not read/copy host memory, skills, settings or other provider
credentials. Pi's own files persist inside the encrypted workspace. Do not use
ordinary /login or API-key pasting inside this container.

Other coding agents can be added to a derived image. Each needs an explicitly
compatible proxy authentication path; being installed is not proof that its
OAuth flow is tokenless. Do not turn on a generic token-vending workaround.
