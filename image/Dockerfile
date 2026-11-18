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
ENV TERRAFORM_VERSION=1.15.8 PYTHON_VERSION=3.14.7 GOGCLI_VERSION=0.48.1 CHECKMARX_VERSION=2.3.65
ENV AUTH0_CLI_VERSION=1.37.0 STRIPE_CLI_VERSION=1.53.1 RESEND_CLI_VERSION=2.28.0

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
    chmod 0644 package.json package-lock.json; \
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
# match the image PATH (/opt/nvim/bin). Checksum-verified, arch-aware.
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


# Additional developer CLIs. Account configuration is supplied privately at exec time.
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) tfarch='arm64'; sha='8891e9dcedc9e3b8950bc6af9d4d8af1f4cfade3062f53b9dc403a89f6ce8c9c' ;; \
      amd64) tfarch='amd64'; sha='d25ce7b6902013ad905db3d2eab0be4cd905887fe88b81a6171b8d5503c31f3d' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/terraform.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${tfarch}.zip"; \
    echo "${sha}  /tmp/terraform.zip" | sha256sum -c -; \
    unzip -q /tmp/terraform.zip -d /usr/local/bin; \
    chmod +x /usr/local/bin/terraform; \
    rm -f /tmp/terraform.zip; \
    terraform version; \
    rm -f /tmp/build-ca.pem

RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    UV_SYSTEM_CERTS=1 \
    UV_PYTHON_INSTALL_DIR=/opt/agent-container-python \
    UV_PYTHON_BIN_DIR=/usr/local/bin \
    uv python install --default --no-cache "$PYTHON_VERSION"; \
    test "$(python3 -c 'import platform; print(platform.python_version())')" = "$PYTHON_VERSION"; \
    python --version; python3 --version; python3.14 --version; \
    rm -f /tmp/build-ca.pem

ARG AZCOPY_VERSION=10.32.4
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) azarch='arm64'; sha='c614777841277ab2c53eecc9ecca5704fd697375c2ffaf4a407058891f00f673' ;; \
      amd64) azarch='amd64'; sha='8f859a0dbbc117660c249fb3569694fc8a0f33b68701f5b2b92ccc001ee50784' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/azcopy.tgz "https://github.com/Azure/azure-storage-azcopy/releases/download/v${AZCOPY_VERSION}/azcopy_linux_${azarch}_${AZCOPY_VERSION}.tar.gz"; \
    echo "${sha}  /tmp/azcopy.tgz" | sha256sum -c -; \
    tar -xzf /tmp/azcopy.tgz -C /tmp; \
    install -m 0755 /tmp/azcopy_linux_${azarch}_${AZCOPY_VERSION}/azcopy /usr/local/bin/azcopy; \
    rm -rf /tmp/azcopy.tgz /tmp/azcopy_linux_*; \
    azcopy --version; \
    rm -f /tmp/build-ca.pem

RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) cxarch='arm64'; sha='456fec50d0627eef730af4d13731785a8d7e242875bab8d09a717d8efd821180' ;; \
      amd64) cxarch='x64';   sha='311f67b21cd0f7c65f10439cc3a3517e8f8457f2fcac3340dd92f9cf07a73789' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    archive="ast-cli_${CHECKMARX_VERSION}_linux_${cxarch}.tar.gz"; \
    curl -fsSLo "/tmp/${archive}" "https://github.com/Checkmarx/ast-cli/releases/download/${CHECKMARX_VERSION}/${archive}"; \
    echo "${sha}  /tmp/${archive}" | sha256sum -c -; \
    tar -xzf "/tmp/${archive}" -C /tmp cx; \
    install -m 0755 /tmp/cx /usr/local/bin/cx-real; \
    rm -f "/tmp/${archive}" /tmp/cx; \
    cx-real version; \
    rm -f /tmp/build-ca.pem

RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    apt-get update && apt-get install -y --no-install-recommends jq; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) gogarch='arm64'; sha='c091fa2014dc3d5608acef4d09fb3bb4a3eb71e4cff898b5eaddd9703599e45c' ;; \
      amd64) gogarch='amd64'; sha='dcd956e09d1222a07c1304d836f8acd55266b2fa241ef466580ced6a02453d72' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/gogcli.tar.gz "https://github.com/openclaw/gogcli/releases/download/v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_linux_${gogarch}.tar.gz"; \
    echo "${sha}  /tmp/gogcli.tar.gz" | sha256sum -c -; \
    mkdir /tmp/gogcli; \
    tar -xzf /tmp/gogcli.tar.gz -C /tmp/gogcli; \
    install -m 0755 /tmp/gogcli/gog /usr/local/bin/gog-real; \
    rm -rf /tmp/gogcli /tmp/gogcli.tar.gz /var/lib/apt/lists/*; \
    gog-real --version | head -1; \
    rm -f /tmp/build-ca.pem

ENV AUTH0_CLI_ANALYTICS=false
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) a0arch='arm64';  sha='e8ebb31520921b84ce462641094f92559dfe9f5edaaa78a58109aac7599478b7' ;; \
      amd64) a0arch='x86_64'; sha='277e8fa32b9e4f4feb8e30717892139251821c0c66cd3ad5f326ba759ec6ac4f' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/auth0.tar.gz "https://github.com/auth0/auth0-cli/releases/download/v${AUTH0_CLI_VERSION}/auth0-cli_${AUTH0_CLI_VERSION}_Linux_${a0arch}.tar.gz"; \
    echo "${sha}  /tmp/auth0.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/auth0.tar.gz -C /usr/local/bin auth0; \
    rm -f /tmp/auth0.tar.gz; \
    auth0 --version; \
    rm -f /tmp/build-ca.pem

ENV STRIPE_CLI_TELEMETRY_OPTOUT=1 STRIPE_NO_AUTO_UPDATE=1
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) starch='arm64';  sha='b6e83f779e7df52b46397c7d39f71b1de6d8920239861a6eaa0da14c24e0a3c6' ;; \
      amd64) starch='x86_64'; sha='032fe0b849a9d6a222488bc846445bafeae993af1026b78a8121d02321ca1fd1' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/stripe.tar.gz "https://github.com/stripe/stripe-cli/releases/download/v${STRIPE_CLI_VERSION}/stripe_${STRIPE_CLI_VERSION}_linux_${starch}.tar.gz"; \
    echo "${sha}  /tmp/stripe.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/stripe.tar.gz -C /usr/local/bin stripe; \
    rm -f /tmp/stripe.tar.gz; \
    stripe version; \
    rm -f /tmp/build-ca.pem

ENV RESEND_TELEMETRY_DISABLED=1 RESEND_NO_UPDATE_NOTIFIER=1
RUN --mount=type=secret,id=upstream_ca set -eu; if [ -f /run/secrets/upstream_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/upstream_ca > /tmp/build-ca.pem; export CURL_CA_BUNDLE=/tmp/build-ca.pem SSL_CERT_FILE=/tmp/build-ca.pem; fi; set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) rsarch='arm64'; sha='a03fa3daada607e39625bc926a72bdfe1302003dbba6f72afe33040e0b5d440c' ;; \
      amd64) rsarch='x64';   sha='bf98fbf20c9beec0beca2a05dfa3eccd2591471a702014caf8f7aacf60e5e523' ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/resend.tar.gz "https://github.com/resend/resend-cli/releases/download/v${RESEND_CLI_VERSION}/resend-linux-${rsarch}.tar.gz"; \
    echo "${sha}  /tmp/resend.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/resend.tar.gz -C /usr/local/bin resend; \
    rm -f /tmp/resend.tar.gz; \
    resend --version; \
    rm -f /tmp/build-ca.pem

COPY runtime/cipher-copy.py /usr/local/bin/cipher-copy
RUN chmod 0755 /usr/local/bin/cipher-copy
COPY runtime/github_auth.py runtime/client_shims.py /usr/local/bin/
COPY runtime/gh-proxy.py /usr/local/bin/gh
COPY runtime/gog-proxy.py /usr/local/bin/gog
COPY runtime/cx-proxy.py /usr/local/bin/cx
RUN chmod 0644 /usr/local/bin/github_auth.py /usr/local/bin/client_shims.py \
    && chmod 0755 /usr/local/bin/gh /usr/local/bin/gog /usr/local/bin/cx

COPY runtime/mount-devcrypt.sh runtime/devcrypt-watchdog.sh runtime/nested-podman-keeper.sh runtime/nested-podman-setup.sh runtime/nested-podman-shutdown.sh runtime/nested-proxy-bridge.sh runtime/git-ssh-sign-proxy.sh /usr/local/bin/
COPY runtime/nested-podman-wrapper.sh /usr/local/bin/podman
COPY runtime/git-credential-proxy.py /usr/local/bin/git-credential-proxy
COPY runtime/setup-home.py /usr/local/bin/setup-home
COPY home /opt/agent-container-home
# Host checkout umask must not make root-owned defaults unreadable to dev.
RUN chmod -R a+rX,go-w /opt/agent-container-home \
    && chmod 0755 /usr/local/bin/*.sh /usr/local/bin/podman /usr/local/bin/git-credential-proxy /usr/local/bin/setup-home \
    && ln -s /usr/local/bin/podman /usr/local/bin/docker
ENV PATH="/opt/nvim/bin:${PATH}" PI_TELEMETRY=0 PI_SKIP_VERSION_CHECK=1
USER dev
WORKDIR /home/dev
CMD ["/usr/local/bin/nested-podman-keeper.sh"]
