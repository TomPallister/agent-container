#!/bin/sh
# gpg.ssh.program for the agent-container container: sign git commits and tags through
# secret-proxy, which holds the SSH signing key (GIT_SIGNING_SSH_KEY in the
# secret store). The private key never enters the container; this script only
# sends the object git wants signed and writes back the armored signature.
#
# git calls it as `ssh-keygen -Y sign -n git -f <public-key-file> [-U] <file>`
# and reads <file>.sig. Every other mode (-Y verify, find-principals,
# check-novalidate) runs the real ssh-keygen, so `git log --show-signature`
# still works against gpg.ssh.allowedSignersFile.
set -eu

real_keygen=${GIT_SSH_SIGN_KEYGEN:-/usr/bin/ssh-keygen}

if [ "${1:-}" != "-Y" ] || [ "${2:-}" != "sign" ]; then
    if [ ! -x "$real_keygen" ]; then
        echo "git-ssh-sign-proxy: $real_keygen is not installed; cannot run ssh-keygen $*" >&2
        exit 1
    fi
    exec "$real_keygen" "$@"
fi
shift 2

namespace=""
key_file=""
target=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -n) namespace=${2:-}; shift 2 ;;
        -f) key_file=${2:-}; shift 2 ;;
        -U) shift ;;
        -*) echo "git-ssh-sign-proxy: unsupported option $1" >&2; exit 1 ;;
        *) target=$1; shift ;;
    esac
done

if [ "$namespace" != git ]; then
    echo "git-ssh-sign-proxy: only the git signature namespace is supported" >&2
    exit 1
fi
if [ -z "$target" ] || [ ! -f "$target" ]; then
    echo "git-ssh-sign-proxy: nothing to sign" >&2
    exit 1
fi
if [ -z "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
    echo "git-ssh-sign-proxy: commit signing needs secret-proxy; run git from a 'dev' shell" >&2
    exit 1
fi

public_key=""
if [ -n "$key_file" ] && [ -f "$key_file" ]; then
    public_key=$(head -n 1 "$key_file" | tr -d '\r\n')
fi

# Assembled so no editor or model artefact can change the reserved host name.
sentinel_host="secre""tref.invalid"
signature="$target.sig"
response=$(mktemp "${TMPDIR:-/tmp}/git-ssh-sign.XXXXXX")
trap 'rm -f "$response"' EXIT HUP INT TERM

status=$(curl -sS --max-time "${GIT_SSH_SIGN_TIMEOUT:-60}" \
    -o "$response" -w '%{http_code}' \
    -H 'Content-Type: application/octet-stream' \
    -H "X-Git-Signing-Key: $public_key" \
    --data-binary "@$target" \
    "https://$sentinel_host/git/sign") || status=000

if [ "$status" != 200 ]; then
    detail=$(head -c 300 "$response" 2>/dev/null || true)
    echo "git-ssh-sign-proxy: secret-proxy refused to sign (HTTP $status) $detail" >&2
    rm -f "$signature"
    exit 1
fi
if ! grep -q '^-----BEGIN SSH SIGNATURE-----$' "$response"; then
    echo "git-ssh-sign-proxy: secret-proxy returned something that is not a signature" >&2
    rm -f "$signature"
    exit 1
fi
mv "$response" "$signature"
trap - EXIT HUP INT TERM
echo "Signed file written to $signature" >&2
