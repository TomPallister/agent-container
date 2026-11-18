# agent-container

An opinionated development container for coding agents, designed to run with
[secret-proxy](https://github.com/TomPallister/secret-proxy).

**secret-proxy holds credentials. agent-container runs your tools.** Together
they provide an encrypted development workspace with proxy-mediated external
access, without sharing your host home directory or container-engine socket.

**Early macOS preview.** Tested on Apple Silicon with Podman/libkrun. Linux/WSL
host integration and Intel execution are not yet validated. This is not an
escape-proof sandbox; read [the security model](SECURITY.md).

## Start here

You need:
- macOS, Apple's Command Line Tools (`xcode-select --install`), and
  [Podman with libkrun/krunkit](https://podman.io/).
- A running, separately installed secret-proxy, configured for the services you
  want to use. This project never installs, restarts or changes its policies.
- A terminal; a Nerd Font is optional for the supplied prompt.

```bash
git clone https://github.com/TomPallister/agent-container.git
cd agent-container
./install.sh
agent-container setup
agent-container
```

Add `~/.local/bin` to PATH if needed. Keep the checkout: the installed command
links to it. Setup discovers your proxy, asks for Git identity and workspace
unlock preferences, creates a dedicated **no-host-shares** development VM,
builds the image and initialises an encrypted volume. First build takes several
minutes. **No prebuilt images are published yet.**

The workspace passphrase is separate from API credentials. Type one, or select
an existing 1Password item in an explicitly selected account. Keep a recoverable
copy in your password manager: losing it means losing the encrypted data.
Setup never creates vault items or asks for an API token.

If setup stops after saving settings, correct the prerequisite, then run
`agent-container build` and `agent-container init`. It will not overwrite
existing configuration or automatically initialise an existing unknown store.

## Included

- Zsh, Oh My Zsh, Powerlevel10k and autosuggestions; Bash is also supported.
- Neovim with small offline defaults, tmux, Git, GitHub CLI and common utilities.
- Node.js, Python, uv, AWS CLI and [Pi](https://pi.dev).
- Encrypted `~/dev`, including Pi configuration/sessions and user shell overrides.
- Rootless nested Podman/Compose, with encrypted storage.
- Automatic forwarding of explicitly published nested TCP ports to host loopback.
- Reference-based GitHub authentication and optional proxy-backed Git signing.

Nothing is copied from your host Pi installation: no private settings, skills,
memory, bots or stored API keys.

## Daily use

```bash
agent-container                   # unlock if needed, enter Zsh
agent-container exec -- git status # execute in ~/dev
agent-container status
agent-container ports
agent-container stop              # stop this container; keep encrypted data
```

Inside:

```bash
git clone https://github.com/YOUR-ORG/YOUR-REPO.git
cd YOUR-REPO
pi
podman compose up -d
```

**Permissions come from your proxy.** Its starter GitHub policy allows API reads,
not private Git clone/push or signing. See [Git and agent integration](docs/integrations.md)
before using those features.

For ChatGPT/Codex, run `secret-proxy login` on the host, then
`agent-container login`. Only the validated **sealed** Codex record is imported.
Select your model with Pi's `/model`. Do not run ordinary OAuth/token login flows
inside the container. Other providers use reference variables from your proxy's
client configuration.

The workspace idle watchdog waits while terminals are open. After the idle
timeout it stops nested workloads and attempts to unmount. A busy workspace
stays mounted rather than being reported as locked. `stop` ends the container.

## Make it yours

Private host settings: `~/.config/agent-container/config.json`.

- Set `shell` to `bash` or `zsh`.
- Add shell customisations in `~/dev/.agent-container/zshrc.local` or
  `bashrc.local` **inside the container**.
- Edit `~/dev/.agent-container/nvim/init.lua` and `pi/settings.json`.
- Use a derived image for additional system tools; see
  [customisation and upgrades](docs/customisation.md).

No general-purpose host mounts, arbitrary container flags or direct-network
fallback are exposed as preferences.

## Updates and data

Update source deliberately, run `agent-container build`, then
`agent-container recreate` when ready to stop your processes. The next entry
uses the new image. Recreation preserves the encrypted volume and configuration
beneath `~/dev`, but discards the writable container home outside it.

**Encryption is not backup.** Push repositories and keep independent backups.
There is intentionally no automatic machine/volume deletion or migration.

## Development

`./test.sh` runs the unit and shell regression suites using uv. The opt-in
[disposable smoke test](docs/development.md) checks actual container behaviour.
No GitHub Actions are used.

Apache-2.0 for first-party code. Bundled/downloaded tools retain their own
licences; see [third-party notices](THIRD_PARTY.md).
