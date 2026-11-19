# Explicit host/workspace transfers

Run from the host, with an already running and unlocked workspace:

```bash
agent-container copy ./project dev:project
agent-container copy dev:project ./project-export
agent-container copy ./file.txt dev:imports/
agent-container move ./finished-project dev:finished-project
```

For a non-default instance, put `--config /path/to/private.json` before `copy`
or `move`. Exactly one copy path must start with `dev:`. It refers to `~/dev`
inside the configured instance, not the host. `dev:~/dev/project` and
`dev:/home/dev/dev/project` also work. Moving is host **directory** to workspace
only; the destination is required. Stop all source writers before a move.

## Deliberately strict defaults

- **New destinations only.** No overwrites or directory merges. A directory is
  copied to the exact destination name, not implicitly nested under it. For a
  single file, a trailing destination slash means append the source filename.
  A host destination that is already a directory also accepts a single new file.
- Missing destination parents are created owner-only. Existing symlink ancestors
  and symlink destinations are refused. Standard macOS `/tmp`, `/var` and `/etc`
  aliases are normalised; arbitrary symlink ancestry is not followed.
- Files, directories, empty directories and relative symlinks confined to the
  copied tree are supported, including checked chains (maximum 40 links).
  Absolute/escaping/cyclic links, source-root symlinks,
  nested filesystem mounts, sockets, FIFOs, devices and special permission bits
  are refused. Hard-linked files become independent files.
- File bytes, names, ordinary permission bits, file/directory modification times
  and safe symlink targets are verified. Ownership, ACLs, extended attributes
  (including macOS resource forks), sparse allocation and hard-link relationships
  are **not** preserved. These exclusions apply to moves too. This is for
  ordinary code/data trees, not a filesystem migration or archival backup; use
  an appropriate archive/backup tool when those properties matter.
- Nothing is started, unlocked, rebuilt or recreated. No proxy login, network
  authentication, Pi bootstrap or bot start occurs. An existing unlocked
  workspace can transfer files while secret-proxy is unavailable.

## Verification and failure behaviour

Data streams through credential-free Podman exec processes. It never uses
`podman cp`, a host mount, an engine socket inside the container, or a temporary
plaintext archive on the host. The container endpoint holds the actual gocryptfs
mount and refreshes ordinary idle activity. It refuses a bare/locked mountpoint.

The receiver accepts a bounded, typed stream, not a tar archive (64 KiB headers,
one million entries and 64 MiB of metadata). File writes check available space
with a best-effort 64 MiB margin; this is not a reservation or disk quota. It stages in a
private sibling directory, does no-follow descriptor-based I/O, re-reads and
hashes the written tree, then publishes with a kernel atomic **no-replace** rename.
Malformed/truncated data, content corruption and changed sources are refused.
Existing destination data is never replaced.

A move additionally compares the destination digest to the host's own manifest,
rechecks runtime identity, rechecks the source, renames that same source inode to
private quarantine, verifies again, and only then deletes it. A changed source
is retained/restored. A failed deletion may leave a partial source **and a full
verified destination**; inspect both instead of retrying blindly. Open writers
cannot be frozen portably: **quiesce them first**, especially databases and build
processes. This is not a point-in-time filesystem snapshot.

Failures attempt to clean up staging. Permission/I/O errors or abrupt engine/host
loss may leave an owner-only `.agent-transfer-*` directory; a failed source restoration names its
`.agent-move-*` recovery path. Inspect these explicitly. No automatic cleanup
removes unrelated files. A failure after publication may leave the complete new
destination, with the source retained.

Downloads intentionally create **plaintext on the chosen host path**. Only export
what you intend to place there. Stream validation prevents archive-style path
traversal; it does not make downloaded content safe to execute. The operator,
selected image and engine remain trusted. A compromised destination can delete
or change its files after verification; a move is not a backup against that.

## Tests

`./test.sh` includes protocol, filesystem, move-retention and dispatcher tests.
`uv run --no-project python tests/transfer_mutations.py` checks negative assertions.
The opt-in `uv run --no-project python tests/smoke_transfers.py --machine NAME`
creates only disposable labelled resources using the already built image, with
no proxy or production-workspace changes. Tests exercise the real Mac/Linux
atomic rename primitives and a real encrypted Linux mount on the Mac engine.
