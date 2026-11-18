#!/usr/bin/env bash
# Configure rootless Podman with encrypted persistent storage and safe networking.
set -euo pipefail

TEST_MODE=${AGENT_CONTAINER_NESTED_TEST_MODE:-0}
case "$TEST_MODE" in 0|1) ;; *) echo 'nested-podman-setup: invalid test mode' >&2; exit 64;; esac
if [ "$TEST_MODE" = 0 ]; then
	for override in DEVCRYPT_PLAIN AGENT_CONTAINER_PODMAN_RUNROOT AGENT_CONTAINER_BOOT_ID_FILE NESTED_PROXY_BRIDGE; do
		[ -z "${!override+x}" ] \
			|| { echo "nested-podman-setup: $override is test-only" >&2; exit 64; }
	done
fi

PLAIN=${DEVCRYPT_PLAIN:-$HOME/dev}
RUNTIME=${AGENT_CONTAINER_PODMAN_RUNROOT:-/tmp/agent-container-podman}
BOOT_ID_FILE=${AGENT_CONTAINER_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}
BRIDGE=${NESTED_PROXY_BRIDGE:-/usr/local/bin/nested-proxy-bridge.sh}
CONFIG_DIR=$HOME/.config/containers
STORAGE_CONFIG=$CONFIG_DIR/storage.conf
CONTAINERS_CONFIG=$CONFIG_DIR/containers.conf
LEGACY_GRAPHROOT=$PLAIN/.podman/storage
GRAPHROOT=$PLAIN/.podman/storage-overlay
CA_SOURCE=/etc/ssl/certs/ca-certificates.crt
CA_TARGET=/etc/agent-container-ca/ca-certificates.crt

mountpoint -q "$PLAIN" \
	|| { echo 'nested-podman-setup: encrypted workspace is not mounted' >&2; exit 1; }
[ -x "$BRIDGE" ] \
	|| { echo 'nested-podman-setup: child proxy bridge is unavailable' >&2; exit 1; }
FUSE_OVERLAYFS=$(command -v fuse-overlayfs) \
	|| { echo 'nested-podman-setup: fuse-overlayfs is unavailable' >&2; exit 1; }
[ -x "$FUSE_OVERLAYFS" ] \
	|| { echo 'nested-podman-setup: fuse-overlayfs is not executable' >&2; exit 1; }
if [ "$TEST_MODE" = 0 ]; then
	[ -s "$CA_SOURCE" ] \
		|| { echo 'nested-podman-setup: parent CA bundle is unavailable' >&2; exit 1; }
fi

umask 077
if [ -d "$LEGACY_GRAPHROOT" ] && [ ! -d "$GRAPHROOT" ]; then
	echo "nested-podman-setup: retaining legacy VFS graphroot at $LEGACY_GRAPHROOT" >&2
fi
mkdir -p "$CONFIG_DIR" "$GRAPHROOT" "$RUNTIME"
chmod 700 "$CONFIG_DIR" "$PLAIN/.podman" "$GRAPHROOT" "$RUNTIME"

current_boot_id=$(cat "$BOOT_ID_FILE") \
	|| { echo 'nested-podman-setup: cannot read host boot ID' >&2; exit 1; }
case "$current_boot_id" in
	????????-????-????-????-????????????) ;;
	*) echo 'nested-podman-setup: invalid host boot ID' >&2; exit 1;;
esac
cached_boot_id=$(cat "$RUNTIME/host-boot-id" 2>/dev/null || true)
if [ -n "$cached_boot_id" ] && [ "$cached_boot_id" != "$current_boot_id" ]; then
	echo 'nested-podman-setup: host reboot detected; clearing stale ephemeral runtime state' >&2
	rm -rf -- "$RUNTIME/runroot" "$RUNTIME/libpod"
fi
mkdir -p "$RUNTIME/runroot" "$RUNTIME/libpod"
chmod 700 "$RUNTIME/runroot" "$RUNTIME/libpod"
boot_id_tmp=$(mktemp "$RUNTIME/.host-boot-id.XXXXXX")
printf '%s\n' "$current_boot_id" >"$boot_id_tmp"
chmod 600 "$boot_id_tmp"
mv -f -- "$boot_id_tmp" "$RUNTIME/host-boot-id"

storage_tmp=$(mktemp "$CONFIG_DIR/.storage.conf.XXXXXX")
containers_tmp=$(mktemp "$CONFIG_DIR/.containers.conf.XXXXXX")
cleanup() { rm -f -- "${storage_tmp:-}" "${containers_tmp:-}"; }
trap cleanup EXIT HUP INT TERM

cat >"$storage_tmp" <<EOF
[storage]
driver="overlay"
runroot="$RUNTIME/runroot"
graphroot="$GRAPHROOT"

[storage.options.overlay]
mount_program="$FUSE_OVERLAYFS"
EOF
cat >"$containers_tmp" <<EOF
[containers]
base_hosts_file="none"
default_sysctls=[]
host_containers_internal_ip="10.0.2.2"
env=[
  "HTTP_PROXY=http://host.containers.internal:10257",
  "HTTPS_PROXY=http://host.containers.internal:10257",
  "http_proxy=http://host.containers.internal:10257",
  "https_proxy=http://host.containers.internal:10257",
  "NO_PROXY=localhost,127.0.0.1",
  "no_proxy=localhost,127.0.0.1",
  "SSL_CERT_FILE=$CA_TARGET",
  "REQUESTS_CA_BUNDLE=$CA_TARGET",
  "NODE_EXTRA_CA_CERTS=$CA_TARGET",
  "AWS_CA_BUNDLE=$CA_TARGET",
  "CURL_CA_BUNDLE=$CA_TARGET",
  "GIT_SSL_CAINFO=$CA_TARGET"
]
mounts=[
  "type=bind,source=$CA_SOURCE,destination=$CA_TARGET,ro",
  "type=bind,source=$CA_SOURCE,destination=/etc/ssl/certs/ca-certificates.crt,ro",
  "type=bind,source=$CA_SOURCE,destination=/cacert.pem,ro"
]

[engine]
cgroup_manager="cgroupfs"
events_logger="file"
runtime="crun"
tmp_dir="$RUNTIME/libpod"

[network]
default_rootless_network_cmd="pasta"
pasta_options=["-a","10.0.2.100","-n","24","-g","10.0.2.2","--map-host-loopback","10.0.2.2"]
EOF
chmod 600 "$storage_tmp" "$containers_tmp"
mv -f -- "$storage_tmp" "$STORAGE_CONFIG"
mv -f -- "$containers_tmp" "$CONTAINERS_CONFIG"
storage_tmp=
containers_tmp=

"$BRIDGE" start
XDG_RUNTIME_DIR="$RUNTIME" podman info >/dev/null \
	|| { echo 'nested-podman-setup: podman info failed' >&2; exit 1; }
# Fail closed when nested Podman's namespace predates the workspace mount: it
# would otherwise write images and containers unencrypted beneath the mountpoint.
XDG_RUNTIME_DIR="$RUNTIME" podman unshare mountpoint -q "$PLAIN" \
	|| { echo 'nested-podman-setup: nested Podman cannot see the encrypted workspace; recreate the container (podman rm -f <container> && agent-container)' >&2; exit 1; }
