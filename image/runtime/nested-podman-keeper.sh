#!/bin/sh
# Create the rootless nested-Podman user namespace from the container's initial
# process context. Processes started later via outer `podman exec` cannot create
# this namespace under keep-id on some kernels/runtimes, but can reuse it.
set -eu

RUNTIME=${AGENT_CONTAINER_PODMAN_RUNROOT:-/tmp/agent-container-podman}
BOOT_ID_FILE=${AGENT_CONTAINER_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}
PODMAN=${AGENT_CONTAINER_PODMAN_BIN:-/usr/bin/podman}
mkdir -p "$RUNTIME"
chmod 700 "$RUNTIME"

# $RUNTIME lives in the container's writable layer, so it survives `podman stop`
# and `podman start` but not a host reboot. Podman refuses to run against runtime
# state from a previous boot ("current system boot ID differs from cached boot
# ID"), so the first start after a reboot exits 125 here.
#
# nested-podman-setup.sh carries the same guard, but it runs later via outer
# `podman exec`, which cannot happen while this process is what fails. The check
# therefore has to be here, before Podman is touched at all.
#
# The refreshed ID is recorded now rather than left to setup, so setup does not
# then delete a runroot this process is already using.
current_boot_id=$(cat "$BOOT_ID_FILE" 2>/dev/null || true)
cached_boot_id=$(cat "$RUNTIME/host-boot-id" 2>/dev/null || true)
if [ -n "$current_boot_id" ] && [ "$cached_boot_id" != "$current_boot_id" ]; then
	if [ -n "$cached_boot_id" ]; then
		echo 'nested-podman-keeper: host reboot detected; clearing stale ephemeral runtime state' >&2
		rm -rf -- "$RUNTIME/runroot" "$RUNTIME/libpod"
	fi
	boot_id_tmp=$(mktemp "$RUNTIME/.host-boot-id.XXXXXX")
	printf '%s\n' "$current_boot_id" >"$boot_id_tmp"
	chmod 600 "$boot_id_tmp"
	mv -f -- "$boot_id_tmp" "$RUNTIME/host-boot-id"
fi

export XDG_RUNTIME_DIR=$RUNTIME

# The namespace created below gets its own copy of the container's mount table.
# A copy of a *private* mount never sees mounts made later, and this process
# necessarily runs before dev.sh mounts the encrypted workspace, so nested Podman
# would see only the bare mountpoint: every `podman` run from inside ~/dev fails
# to chdir, and its storage lands unencrypted beneath the mount. Observed on
# 2026-10-08 after the containers were restarted: `podman compose` reported
# Podman as not installed. dev.sh makes the container's root mount shared as
# root (this process runs as dev and cannot); the copy is then a slave, so the
# later mount and every idle unmount propagate into it. Wait for that first.
MOUNTINFO=${AGENT_CONTAINER_MOUNTINFO:-/proc/self/mountinfo}
root_mount_is_shared() {
	awk '$5 == "/" { for (i = 7; i <= NF && $i != "-"; i++) if ($i ~ /^shared:/) found = 1 }
		END { exit !found }' "$MOUNTINFO" 2>/dev/null
}
if ! root_mount_is_shared; then
	echo 'nested-podman-keeper: waiting for dev to share the root mount' >&2
	until root_mount_is_shared; do sleep 0.2; done
fi

# `podman unshare` initialises storage eagerly, and nested-podman-setup.sh puts the
# graphroot inside the encrypted workspace. This process is PID 1, so it necessarily
# runs before dev.sh can mount that workspace: with the graphroot configured, every
# start after the first recreates the storage tree in the *bare* mountpoint, and
# gocryptfs then refuses to mount over it ("Invalid mountpoint: directory not
# empty"). A brand-new container has no storage.conf yet, which is why this only
# appears once the container has been stopped and started again.
#
# nested-podman-wrapper.sh guards the same hazard for ordinary callers, but the
# keeper has to bypass the wrapper, so it needs its own guarantee. Overriding only
# --root keeps runroot from the real configuration, so the user namespace this
# process creates is still where later Podman invocations look for it.
KEEPER_GRAPHROOT=$RUNTIME/keeper-graphroot
mkdir -p "$KEEPER_GRAPHROOT"
chmod 700 "$KEEPER_GRAPHROOT"

exec "$PODMAN" --root "$KEEPER_GRAPHROOT" unshare /bin/sleep infinity
