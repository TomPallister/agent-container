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

1. Host: `secret-proxy login chatgpt` (browser approval; real tokens remain in the proxy).
2. Host: `agent-container login` (validates and copies only the sealed Codex entry).
3. Container: `pi`, then `/model`.

The import does not read/copy host memory, skills, settings or other provider
credentials. Pi's own files persist inside the encrypted workspace. Do not use
ordinary /login or API-key pasting inside this container.

## Proxy login and policy compatibility

Recent secret-proxy versions require a provider in `login`, `auth-status` and
`logout`: `chatgpt`, `github-copilot` or `github`. An optional separate login
vault stores long-lived tokens inside the proxy's trust boundary. First use or
`auth-status` may restore a saved login after restart. Without it, log in again.
These choices do not change the workspace's reference-only credential model.

- **GitHub CLI:** enable the proxy's gh login and call `secret-proxy login github`
  on the host. Set the relevant `githubSecret` / `githubOwners` mapping to
  `GH_CLI_OAUTH_TOKEN` instead of a vault API token, with reviewed placements.
  GraphQL is POST even for read queries; granting that endpoint also permits
  mutations unless a separate policy prevents them.
- **Copilot in Pi:** the proxy can issue sealed Copilot records, but
  `agent-container login` currently imports **only Codex**, not Copilot or the
  whole host auth file. Configure another supported sealed-only consumer
  deliberately; never fall back to an ordinary in-container device login.
  `githubCopilotSecret` routes GitHub Copilot **administration API calls by gh**;
  it is not a Pi model-login importer.
- **Scoped clients:** the current container adapter uses the default proxy
  capability. Upstream `--client NAME` support does not automatically give this
  workspace a named-client selection.
- **AWS, approvals, egress and audit:** these are proxy policies, not container
  preferences. Configure the intended grants, keep an approver listening when
  required, and expect policy refusals rather than broader permissions.
- **CA changes:** the container installs the proxy's public trust bundle on
  connection. Long-lived clients may cache trust and need a coordinated restart.
  Host `secret-proxy trust-ca` affects the login keychain, not the container's
  Linux certificate store. No TLS-verification bypass is needed.

Google and Checkmarx wrappers use the proxy's derived/sealed-token integrations;
they do not authorise client-side OAuth exchanges. Their policies and reference
variables must already be configured. See the upstream
[client guide](https://github.com/TomPallister/secret-proxy/blob/main/docs/clients.md).

Other coding agents can be added to a derived image. Each needs an explicitly
compatible proxy authentication path; being installed is not proof that its
OAuth flow is tokenless. Do not turn on a generic token-vending workaround.
