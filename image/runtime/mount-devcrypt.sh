#!/usr/bin/env bash
# Explicit initialization/mount helper for agent-container's encrypted ~/dev workspace.
# Passphrases are accepted only on stdin and are never logged or placed in argv.
set -euo pipefail

CIPHER="${DEVCRYPT_CIPHER:-$HOME/.devcipher}"
PLAIN="${DEVCRYPT_PLAIN:-$HOME/dev}"
WATCHDOG="/usr/local/bin/devcrypt-watchdog.sh"
MODE=${1:-mount}
case "$MODE" in init|mount) ;; *) echo "usage: $0 {init|mount}" >&2; exit 64;; esac

mkdir -p "$CIPHER" "$PLAIN"

if [ "$MODE" = init ]; then
	if [ -f "$CIPHER/gocryptfs.conf" ]; then
		echo "devcrypt: encrypted store is already initialized at $CIPHER"
		exit 0
	fi
	if mountpoint -q "$PLAIN"; then
		echo "devcrypt: cannot initialize while $PLAIN is mounted" >&2
		exit 1
	fi
	IFS= read -r PASS || true
	if [ -z "${PASS:-}" ]; then
		echo "devcrypt: no passphrase on stdin — refusing to initialize." >&2
		exit 2
	fi
	echo "devcrypt: initializing encrypted store at $CIPHER"
	printf '%s' "$PASS" | gocryptfs -init -passfile /dev/stdin "$CIPHER"
	PASS=
	[ -f "$CIPHER/gocryptfs.conf" ] \
		|| { echo "devcrypt: initialization did not create gocryptfs.conf" >&2; exit 1; }
	echo "devcrypt: initialized $CIPHER (run 'dev' to mount it)"
	exit 0
fi

if mountpoint -q "$PLAIN"; then
	echo "devcrypt: already mounted at $PLAIN"
	if [ -x "$WATCHDOG" ]; then "$WATCHDOG" start >/dev/null; fi
	exit 0
fi
if [ ! -f "$CIPHER/gocryptfs.conf" ]; then
	echo "devcrypt: encrypted store is not initialized — run 'agent-container init' first." >&2
	exit 3
fi

IFS= read -r PASS || true
if [ -z "${PASS:-}" ]; then
	echo "devcrypt: no passphrase on stdin — refusing to mount (run via 'dev')." >&2
	exit 2
fi

# Never delete unexpected data beneath an unmounted workspace.
[ -z "$(ls -A "$PLAIN")" ] || { echo "devcrypt: non-empty unmounted workspace; inspect it before mounting" >&2; exit 4; }

echo "devcrypt: mounting $CIPHER -> $PLAIN"
# Nested Podman switches to subordinate UIDs for ordinary non-root images.
# allow_other lets those identities execute their isolated rootfs beneath the
# encrypted graphroot; the parent workspace is still absent from their mount
# namespace unless explicitly bind-mounted.
printf '%s' "$PASS" | gocryptfs -allow_other -passfile /dev/stdin "$CIPHER" "$PLAIN"
PASS=
mountpoint -q "$PLAIN" \
	|| { echo "devcrypt: gocryptfs returned without mounting $PLAIN" >&2; exit 1; }
if [ -x "$WATCHDOG" ]; then "$WATCHDOG" start >/dev/null; fi
echo "devcrypt: mounted $PLAIN"
