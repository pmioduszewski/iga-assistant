# Git hooks — local secret guard

`pre-commit` and `pre-push` run **ggshield** (the same engine GitGuardian
runs on PRs) so secrets are caught *before* a commit object exists — long
before anything is pushed. Triaged false positives live in
`../.gitguardian.yaml` (each with a written reason); the hooks never weaken
detection.

## Activate (once per clone — hooks dirs aren't auto-enabled by git)

```sh
git config core.hooksPath .githooks
brew install ggshield   # if not already present
```

Verify: `git config core.hooksPath` → `.githooks`.

Emergency bypass: `git commit --no-verify` (the server-side PR check still
runs regardless, so this only defers the gate, never removes it).
