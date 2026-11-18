# Customisation and upgrades

Setup writes a 0600 JSON file in ~/.config/agent-container. Keep it outside Git.
Unknown keys and arbitrary container flags are rejected.

- `shell`: zsh (default) or bash.
- `git.name`, `git.email`: your identity, written to the encrypted Git config.
- `git.signingKey`: empty, or an SSH Ed25519 PUBLIC key matching the key held by
  your proxy. An empty value means proxy signing is not configured; setting one
  enables commit and annotated-tag signing without fallback.
- `githubSecret`: canonical secret NAME, not a value. Blank disables the helper.
- `proxyConfig`: absolute path to the installed proxy's client.json.
- `buildCA`: optional absolute public PEM bundle, used as a temporary build
  secret. It is not copied into the source or retained by a build layer.
- `unlock`: `{"method":"manual"}`, or
  `{"method":"1password","account":"example.1password.com","reference":"op://Private/Workspace/password"}`.
- `image`: normally localhost/agent-container:dev; advanced users can select a
  locally built derived image.
- `name`, `machine`: determine isolated resource names. Do not change these to
  adopt another application's containers or volumes. They are not migration tools.

## User configuration

Inside the mounted workspace, ~/.agent-container is NOT used.
The persistent directory is **~/dev/.agent-container**:

| Path | Purpose |
|---|---|
| zshrc.local / bashrc.local | Sourced after supplied shell defaults |
| zsh_history / bash_history | Shell history |
| nvim/ | Editable editor configuration |
| nvim-data/ | Editor plugin/runtime data |
| pi/ | Pi settings, skills, extensions, sessions and sealed login |
| gitconfig | Git configuration; setup owns only name/email/default branch |

Add your own Pi packages/skills explicitly. No private team package is installed
automatically. These resources can execute code with the same access as you.

## Toolchain images

Build the standard image first, then create a reviewed Containerfile:

```dockerfile
FROM localhost/agent-container:dev
USER root
RUN apt-get update && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/*
USER dev
```

Build with `podman --connection agent-container build --http-proxy=false -t localhost/team-agent:dev .`
on the host, then select that image in private settings. Keep the image's dev
user, runtime helpers and entrypoint contract. Do not add host credentials.

Host builds have network access and execute the Containerfile you select; they
are trusted operator activity, not agent-restricted runtime activity.
Inside the container, nested builds use the credential-free bridge and public
CA. Put additional system tools in images rather than relying on runtime apt.

## Lifecycle

Source/image changes never silently recreate an existing container. Build,
save work, then explicitly recreate. Only ~/dev survives recreation. The proxy,
its keys, policy and lifecycle are independent.

The launcher creates a dedicated libkrun machine with no host shares and never
changes your default Podman connection. An existing machine with shares is
refused, never deleted. Stop/remove machines and volumes only as an explicit
operator maintenance action after backup.

Changing proxy registration/port may require recreating this instance's
transport relay; stop using the old connection first. Do not remove unrelated
containers. A regular proxy restart with unchanged port/CA is picked up on the
next entry; use fresh shells after a capability/CA replacement.

Use Git to bring repositories into this first preview. A guarded host-file transfer
command and existing-volume migration are not included yet. Do not use Podman cp
as a shortcut into the encrypted mount: engine-side copying may bypass FUSE.

VS Code can attach to the running container through the named Podman connection.
Use agent-container shells for authenticated commands: bare attached terminals
do not automatically receive per-exec proxy capabilities.

## Private service configuration

Optional `environment` settings in the private host config supplement the
proxy client metadata. Credential-shaped values must be canonical references;
loader/trust/proxy overrides are refused. No host environment is inherited.
Use `githubOwners` (lowercase owner to canonical secret name) and optional
`githubCopilotSecret` for owner/API-specific routing. The existing
`githubSecret` remains the default. Account names never belong in public source.

The `gh`/Git helpers select references, not tokens. `GH_SECRET_NAME` selects an
explicit canonical name. `gog` uses a Google access-token reference, refuses
local auth/token options and never performs OAuth. `cx` requires explicit
`CX_BASE_URI` (or `CX_BASE_AUTH_URI`), `CX_TENANT` and a Checkmarx key
reference; both returned tokens must be sealed. These adapters require matching
secret-proxy providers/policies, which the launcher never adds or widens.

`awsrole ACCOUNT ROLE` sets the dummy role selector in Zsh/Bash; the proxy
still enforces authorization. `zshenv.local` is sourced from the encrypted
customisation directory for noninteractive Zsh too. `~/.pi/state` now lives at
`~/dev/.agent-container/pi-state`, alongside encrypted agent and session data.

See [encrypted-volume migration](migration.md) before moving an existing store.
