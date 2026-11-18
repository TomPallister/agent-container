# Existing encrypted workspaces

`agent-container import-volume SOURCE_VOLUME` copies an **offline** gocryptfs
store into a **new instance-owned volume** on the configured Podman connection.
It never adopts, mounts, deletes or writes the original. Build the selected image
first and use a new instance name/private configuration.

1. Back up irreplaceable work. Preserve the old container, configuration and
   validated sealed login separately; its writable home is not in the volume.
2. Stop applications, editors and bots cleanly, then unmount the old plaintext
   workspace and stop every container referencing its encrypted volume.
3. Run `agent-container --config /absolute/private/settings.json import-volume SOURCE_VOLUME`.
4. Unlock using the **existing** passphrase. Do not generate a new passphrase.
5. Restore private configuration/state links. Keep bots disabled until the old
   writers are stopped and the new environment is accepted.
6. Verify actual tools, authentication, persisted files, sessions and recovery
   before changing everyday launchers.

The copy uses a read-only source mount and a network-disabled helper. It verifies
file contents, paths, ownership, modes, mtimes, hardlink counts, symlinks, device
numbers and extended attributes against before/after manifests. FIFO/socket
inodes and inert overlay whiteouts (character device 0,0) from stopped nested
containers are preserved without opening them; other device nodes are refused.
The temporary rootless helper has CHOWN/DAC_OVERRIDE/FOWNER/MKNOD for metadata
preservation, not host privileges or access to a host directory.

Import is refused when the source is running, the destination already exists, or
verification fails. A private receipt beside the settings records completion.
Failed copies remain for inspection and cannot be mounted by this launcher.
There is no automatic cleanup/retry-overwrite. Do not start source writers during
copy; the launcher rechecks source use/identity afterward, but cannot lock an
unrelated external launcher.

Only the ciphertext volume is copied. The application must map older session,
configuration and bot-state paths to `~/dev/.agent-container` explicitly; this
project neither discovers private repositories nor enables bots.

The old volume is a **point-in-time fallback**, not a bidirectional replica.
After doing new work, keep/export those changes before returning to the old
environment. Never run both copies' bots against the same external inputs.
No proxy restart, new vault credential, public policy change, host mount or
engine socket is involved.
