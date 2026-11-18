#!/usr/bin/env bash
# Add the credential-free child proxy and public CA to nested image builds.
set -euo pipefail

TEST_MODE=${AGENT_CONTAINER_NESTED_TEST_MODE:-0}
case "$TEST_MODE" in 0|1) ;; *) echo 'nested-podman-wrapper: invalid test mode' >&2; exit 64;; esac
if [ "$TEST_MODE" = 0 ]; then
	for override in NESTED_PODMAN_REAL NESTED_CA_SOURCE DEVCRYPT_PLAIN; do
		[ -z "${!override+x}" ] \
			|| { echo "nested-podman-wrapper: $override is test-only" >&2; exit 64; }
	done
fi

PODMAN_REAL=${NESTED_PODMAN_REAL:-/usr/bin/podman}
CA_SOURCE=${NESTED_CA_SOURCE:-/etc/ssl/certs/ca-certificates.crt}
CA_TARGET=/etc/ssl/certs/ca-certificates.crt
CHILD_PROXY=http://host.containers.internal:10257

[ -x "$PODMAN_REAL" ] \
	|| { echo 'nested-podman-wrapper: Podman executable is unavailable' >&2; exit 1; }

# Podman creates its graphroot eagerly, and nested-podman-setup.sh deliberately
# places that graphroot inside the encrypted workspace. Any invocation made while
# the workspace is unmounted therefore recreates the storage tree in the *bare*
# mountpoint, after which gocryptfs refuses to mount over it at all ("Invalid
# mountpoint: directory not empty") and `dev` stays broken until the directory is
# cleared by hand. A status poller running `podman ps` is enough to trigger it, so
# refuse up front, before anything can touch the path.
PLAIN=${DEVCRYPT_PLAIN:-$HOME/dev}
STORAGE_CONF=$HOME/.config/containers/storage.conf
# Before nested-podman-setup.sh has written storage.conf, nothing may run either.
# Podman would initialise the default, unencrypted ~/.local/share/containers, and
# the first invocation creates the rootless user and mount namespace that every
# later one joins, including the keeper's. Created before dev.sh shares the root
# mount, that namespace keeps a private copy of the mount table and never sees
# the encrypted workspace. Observed on 2026-10-11: the port reconciler snapshotted
# a freshly started container and setup then failed with "nested Podman cannot
# see the encrypted workspace".
[ -f "$STORAGE_CONF" ] \
	|| { echo 'nested-podman-wrapper: nested Podman is not set up yet (dev sets it up after mounting the workspace) — refusing to run' >&2; exit 1; }
if grep -qF "graphroot=\"$PLAIN/" "$STORAGE_CONF"; then
	mountpoint -q "$PLAIN" \
		|| { echo 'nested-podman-wrapper: encrypted workspace is not mounted — refusing to run nested Podman (its storage lives inside it)' >&2; exit 1; }
fi

# All non-build behavior remains native Podman. podman-compose resolves `podman`
# through PATH, so its generated `podman build` commands take this branch too.
if [ "${1:-}" != build ]; then
	exec "$PODMAN_REAL" "$@"
fi
[ -s "$CA_SOURCE" ] \
	|| { echo 'nested-podman-wrapper: parent CA bundle is unavailable' >&2; exit 1; }
shift

exec "$PODMAN_REAL" build \
	--network=pasta \
	--build-arg "HTTP_PROXY=$CHILD_PROXY" \
	--build-arg "HTTPS_PROXY=$CHILD_PROXY" \
	--build-arg "http_proxy=$CHILD_PROXY" \
	--build-arg "https_proxy=$CHILD_PROXY" \
	--build-arg 'NO_PROXY=localhost,127.0.0.1' \
	--build-arg 'no_proxy=localhost,127.0.0.1' \
	--volume "$CA_SOURCE:$CA_TARGET:ro" \
	--env "SSL_CERT_FILE=$CA_TARGET" \
	--env "REQUESTS_CA_BUNDLE=$CA_TARGET" \
	--env "NODE_EXTRA_CA_CERTS=$CA_TARGET" \
	--env "AWS_CA_BUNDLE=$CA_TARGET" \
	--env "CURL_CA_BUNDLE=$CA_TARGET" \
	--env "GIT_SSL_CAINFO=$CA_TARGET" \
	"$@"
