# Pinned base image (Ubuntu 26.04 LTS) by multi-arch index digest. The index
# digest fixes the starting filesystem while selecting the correct architecture
# (arm64 on Mac, amd64 on WSL/Linux). Ubuntu apt repositories remain mutable,
# so package layers are not bit-for-bit reproducible until a dated snapshot or
# exact package-version policy is adopted.
FROM ubuntu:26.04@sha256:678c6550cc43645e08669028bc177f50be4e7c5b8cca677067b1914d4afc7a03

# Pinned tool versions (no :latest anywhere).
ENV NODE_VERSION=26.3.1
ENV AWSCLI_VERSION=2.36.19
ENV NVIM_VERSION=0.12.4
ENV UV_VERSION=0.12.3
ENV GH_VERSION=2.97.0

# Base packages from the current Ubuntu 26.04 repositories (not snapshot-pinned).
RUN apt-get update && apt-get install -y \
    zsh \
    python3 \
    openssh-client \
    curl \
    git \
    ca-certificates \
    xz-utils \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Marker so shell config can detect it is running inside the dev container
# (used to enable the secret-proxy proxy + CA trust only inside the container).
RUN touch /etc/agent-container-container

# Shell framework/theme/plugin — immutable reviewed revisions, fetched as
# checksum-verified archives and baked under /opt. Runtime setup only links this
# tree; it never executes a remote installer or follows a mutable branch.
ARG OH_MY_ZSH_REV=146461f7c6d95f4ba1220559d66eb113418b40a8
ARG OH_MY_ZSH_SHA256=23fd754895813e0f81293983c7600b7ac87faf69c0734514f6bc42f2f64b7e99
ARG POWERLEVEL10K_REV=3308262dfbd743b6e1d3956a2b5572f7a049d692
ARG POWERLEVEL10K_SHA256=0a6cec61e16328b6e8c974dcfa83d0db2b3745e4e269051e938f028c44b75888
ARG ZSH_AUTOSUGGESTIONS_REV=85919cd1ffa7d2d5412f6d3fe437ebdbeeec4fc5
ARG ZSH_AUTOSUGGESTIONS_SHA256=20665e469e720e674964a66854d5534b9af78cdfd438512746ecd1ade8e819ce
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    set -eux; \
    curl -fsSLo /tmp/oh-my-zsh.tar.gz "https://github.com/ohmyzsh/ohmyzsh/archive/${OH_MY_ZSH_REV}.tar.gz"; \
    curl -fsSLo /tmp/powerlevel10k.tar.gz "https://github.com/romkatv/powerlevel10k/archive/${POWERLEVEL10K_REV}.tar.gz"; \
    curl -fsSLo /tmp/zsh-autosuggestions.tar.gz "https://github.com/zsh-users/zsh-autosuggestions/archive/${ZSH_AUTOSUGGESTIONS_REV}.tar.gz"; \
    printf '%s  %s\n' \
      "$OH_MY_ZSH_SHA256" /tmp/oh-my-zsh.tar.gz \
      "$POWERLEVEL10K_SHA256" /tmp/powerlevel10k.tar.gz \
      "$ZSH_AUTOSUGGESTIONS_SHA256" /tmp/zsh-autosuggestions.tar.gz \
      | sha256sum -c -; \
    mkdir -p /opt/agent-container-zsh/oh-my-zsh; \
    tar -xzf /tmp/oh-my-zsh.tar.gz -C /opt/agent-container-zsh/oh-my-zsh --strip-components=1 --no-same-owner; \
    mkdir -p /opt/agent-container-zsh/oh-my-zsh/custom/themes/powerlevel10k; \
    tar -xzf /tmp/powerlevel10k.tar.gz -C /opt/agent-container-zsh/oh-my-zsh/custom/themes/powerlevel10k --strip-components=1 --no-same-owner; \
    mkdir -p /opt/agent-container-zsh/oh-my-zsh/custom/plugins/zsh-autosuggestions; \
    tar -xzf /tmp/zsh-autosuggestions.tar.gz -C /opt/agent-container-zsh/oh-my-zsh/custom/plugins/zsh-autosuggestions --strip-components=1 --no-same-owner; \
    rm -f /tmp/oh-my-zsh.tar.gz /tmp/powerlevel10k.tar.gz /tmp/zsh-autosuggestions.tar.gz; \
    chmod -R a+rX,go-w /opt/agent-container-zsh; \
    rm -f /tmp/build-ca.pem

# Node.js — pinned version, official binary, checksum-verified, arch-aware
# (arm64 on Apple Silicon today; amd64 on an AWS Linux VM later).
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) nodearch='arm64'; sha='c021380e64d1314d1218ab1f31e0f5b0f28f1f54ac779ef72a16c2bda0ca5c30' ;; \
      amd64) nodearch='x64';   sha='55647180e4ae58ffeaa3294e89aa4abda7c371dfbd64b44cbdb022980177aae0' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${nodearch}.tar.xz"; \
    echo "${sha}  /tmp/node.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner; \
    rm /tmp/node.tar.xz; \
    node --version; npm --version; \
    rm -f /tmp/build-ca.pem

# Pi only: no private profile, extensions or bots. Dependencies are lockfile-pinned.
WORKDIR /opt/agent-container-node-tools
COPY node-tools/package.json node-tools/package-lock.json ./
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    npm ci --omit=dev --ignore-scripts --no-audit --no-fund; \
    ln -s /opt/agent-container-node-tools/node_modules/.bin/pi /usr/local/bin/pi; \
    rm -f /tmp/build-ca.pem
WORKDIR /
# AWS CLI v2 — pinned, official installer, checksum-verified, arch-aware.
# mandoc renders `aws ... help` output and is smaller than the groff alternative.
# Added as a late layer so the Node/pi layers stay cached across rebuilds.
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    set -eux; \
    apt-get update && apt-get install -y --no-install-recommends unzip mandoc; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) awsarch='aarch64'; sha='fb7a8cfd2a516a6b15560582fdc98c417fedffd39728d46fb6007b56b7c858d3' ;; \
      amd64) awsarch='x86_64'; sha='545f9bf0cd58e0f30f3a9470d3c4e3387148006bf30f41fb3a4a167347be815f' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/awscli.zip "https://awscli.amazonaws.com/awscli-exe-linux-${awsarch}-${AWSCLI_VERSION}.zip"; \
    echo "${sha}  /tmp/awscli.zip" | sha256sum -c -; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/awscli.zip /tmp/aws /var/lib/apt/lists/*; \
    aws --version; \
    AWS_PAGER=cat aws sts help >/dev/null; \
    rm -f /tmp/build-ca.pem


# uv (Python package/deps manager) — pinned, official release tarball from
# astral-sh/uv, checksum-verified, arch-aware. Installs both `uv` and `uvx`.
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) uvtarget='aarch64-unknown-linux-gnu'; sha='bb66cb52e7b1823aed1183630d8d8e5c958840d584a4c55ec10a4cfc168dcca2' ;; \
      amd64) uvtarget='x86_64-unknown-linux-gnu';  sha='600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/uv.tar.gz "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${uvtarget}.tar.gz"; \
    echo "${sha}  /tmp/uv.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 --no-same-owner; \
    rm -f /tmp/uv.tar.gz; \
    uv --version; uvx --version; \
    rm -f /tmp/build-ca.pem


# GitHub CLI (gh) — pinned, official release .deb, checksum-verified, arch-aware.
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) sha='0ba7a76739c865d82ebde24667d875d9b8caa55db47c7597c24accdd4defd2bb' ;; \
      amd64) sha='7c7fa3bb890db0934baf65910d97b8c0fa437b2e590f7f7daf6bdf82c5c486d7' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/gh.deb "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.deb"; \
    echo "${sha}  /tmp/gh.deb" | sha256sum -c -; \
    dpkg -i /tmp/gh.deb; \
    rm -f /tmp/gh.deb; \
    gh --version | head -1; \
    rm -f /tmp/build-ca.pem


# gocryptfs + FUSE — the container's ~/dev workspace is an encrypted gocryptfs
# mount. Ciphertext lives in a named volume; plaintext exists only inside the
# container; the passphrase is supplied by the host on stdin (never stored).
# From the current Ubuntu 26.04 repositories (arch-agnostic via apt).
RUN apt-get update && apt-get install -y --no-install-recommends \
    aardvark-dns \
    busybox-static \
    fuse3 \
    fuse-overlayfs \
    gocryptfs \
    nftables \
    passt \
    podman \
    podman-compose \
    socat \
    tinyproxy \
    uidmap \
    && printf '%s\n' user_allow_other > /etc/fuse.conf \
    && rm -rf /var/lib/apt/lists/* \
    && gocryptfs --version | head -1 \
    && fuse-overlayfs --version \
    && podman --version \
    && podman-compose version

# Editor toolchain for the dotfiles nvim/tmux setup: tmux, ripgrep (telescope),
# fd-find (telescope; Debian names the binary `fdfind`, so expose it as `fd`),
# build-essential (treesitter/mason compile parsers & language servers), zip
# (archive creation; unzip is already installed above). From the digest-pinned
# current Ubuntu repositories; apt package versions are not snapshot-pinned.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    ripgrep \
    fd-find \
    build-essential \
    zip \
    && ln -sf "$(command -v fdfind)" /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# Common CLI / network diagnostic tools, baked in so they're always present.
# Keep runtime tooling reproducible and image-reviewed rather than installing it
# ad hoc in the hardened container. Package names verified against
# the Ubuntu 26.04 repo: `dig`/`nslookup` come from bind9-dnsutils (the old
# `dnsutils` transitional package no longer exists).
RUN apt-get update && apt-get install -y --no-install-recommends \
    bind9-dnsutils \
    whois \
    iputils-ping \
    traceroute \
    mtr-tiny \
    netcat-openbsd \
    iproute2 \
    wget \
    rsync \
    tree \
    file \
    htop \
    ncdu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with stable UID/GID 1000 so Docker bind mounts from a
# normal Linux/WSL user are writable. Ubuntu 26.04 images include an `ubuntu`
# user at UID 1000, so remove it first and claim the UID for `dev`.
#
# The outer rootless keep-id namespace maps container IDs 1-999 and 1001-65536,
# not the useradd default 100000-165535. Authorize those two ranges so nested
# newuidmap/newgidmap can construct a complete 0-65535 identity map without
# requesting IDs absent from the outer namespace.
RUN set -eux; \
    if getent passwd ubuntu >/dev/null; then userdel -r ubuntu || userdel ubuntu; fi; \
    if getent group 1000 >/dev/null; then groupdel "$(getent group 1000 | cut -d: -f1)" || true; fi; \
    groupadd -g 1000 dev; \
    useradd -m -u 1000 -g dev -s /bin/zsh dev; \
    rm -f /home/dev/.bashrc; \
    printf 'dev:1:999\ndev:1001:64536\n' > /etc/subuid; \
    printf 'dev:1:999\ndev:1001:64536\n' > /etc/subgid; \
    mkdir -p /home/dev/dev; \
    chown -R dev:dev /home/dev

# Neovim (your $EDITOR) — pinned official tarball installed to /opt/nvim to
# match the .zshenv PATH ($HOME/.local/nvim/bin). Checksum-verified, arch-aware.
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem NODE_EXTRA_CA_CERTS=/tmp/build-ca.pem; fi; \
    set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) nvimarch='arm64';  sha='ceb7e88c6b681f0515d135dcdfad54f5eb4373b25ce6172197cd9a69c758063f' ;; \
      amd64) nvimarch='x86_64'; sha='012bf3fcac5ade43914df3f174668bf64d05e049a4f032a388c027b1ebd78628' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/nvim.tar.gz "https://github.com/neovim/neovim/releases/download/v${NVIM_VERSION}/nvim-linux-${nvimarch}.tar.gz"; \
    echo "${sha}  /tmp/nvim.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/nvim; \
    tar -xzf /tmp/nvim.tar.gz -C /opt/nvim --strip-components=1 --no-same-owner; \
    rm /tmp/nvim.tar.gz; \
    /opt/nvim/bin/nvim --version | head -1; \
    rm -f /tmp/build-ca.pem


COPY runtime/mount-devcrypt.sh runtime/devcrypt-watchdog.sh runtime/nested-podman-keeper.sh runtime/nested-podman-setup.sh runtime/nested-podman-shutdown.sh runtime/nested-proxy-bridge.sh runtime/git-ssh-sign-proxy.sh /usr/local/bin/
COPY runtime/nested-podman-wrapper.sh /usr/local/bin/podman
COPY runtime/git-credential-proxy.py /usr/local/bin/git-credential-proxy
COPY runtime/setup-home.py /usr/local/bin/setup-home
COPY home /opt/agent-container-home
RUN chmod 0755 /usr/local/bin/*.sh /usr/local/bin/podman /usr/local/bin/git-credential-proxy /usr/local/bin/setup-home \
    && ln -s /usr/local/bin/podman /usr/local/bin/docker
ENV PATH="/opt/nvim/bin:${PATH}" PI_TELEMETRY=0 PI_SKIP_VERSION_CHECK=1
USER dev
WORKDIR /home/dev
CMD ["/usr/local/bin/nested-podman-keeper.sh"]
