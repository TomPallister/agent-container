# Third-party software

Apache-2.0 applies to this project's first-party launcher, integration code and
documentation. It does not relicense downloaded tools or their dependencies.

The image builds from Ubuntu and installs distribution packages (including
gocryptfs, FUSE, Podman, Git and shell utilities), plus checksum-verified Node.js,
uv, AWS CLI, GitHub CLI and Neovim releases. Pi and its npm dependencies are
lockfile-pinned. Oh My Zsh, Powerlevel10k and zsh-autosuggestions are downloaded
from pinned revisions with their upstream licence files retained.

The Powerlevel10k preference file is based on the upstream configuration template.
Runtime integration scripts originated in the author's own development setup;
no private dotfiles history, credentials or organisation configuration is included.

Refer to upstream projects and the installed package licence/copyright files
for their terms, source and notices. This initial release publishes source only,
not container archives or binaries. Re-distributing a built image may impose
additional notice/source obligations; review these before publishing images.

Version pins are not a claim that dependencies are free of known vulnerabilities.
