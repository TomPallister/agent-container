# Contributing

- Work in an isolated git worktree; do not modify a live installation.
- Keep this generic. No personal/company identities, private account metadata,
  credentials, proxy state, auth files or real certificates belong in Git.
- secret-proxy owns credentials, policy and lifecycle. Do not copy that engine.
- Fail closed on missing proxy/trust/readiness. Never silently add direct egress,
  host mounts, a host engine socket or unsigned commit fallback.
- Never delete or recreate an existing machine/volume automatically.
- Use uv for Python tooling. Keep executable scripts executable.
- Run ./test.sh, syntax/lint checks, and the disposable smoke for runtime changes.
  Mutation-test new security assertions. Report untested platforms honestly.
- Use Conventional Commits; never bypass signing for real commits.
- Never use GitHub Actions. Publication/deployment needs operator approval.
