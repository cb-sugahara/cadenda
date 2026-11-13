# Local CI / staging adapter

A CI runner invokes only owner-configured checks, with no prompt-supplied command:

```
aiops --config /absolute/projects.yaml ops ci PROJECT ISSUE
```

Exit 0 means the configured checks finished and a Review run was recorded. This
is not a human approval or deployment authorization. Capture stdout JSON and
Recorder artifacts as the CI result. Read-only provider health is available via
`ops health`; `--probe` checks native metadata without inference or signing in.

Local staging is closed by default (`staging_enabled: false`). It requires a
new independent review that records requirement hashes, the latest artifact
acknowledgement, and a separate paired-owner confirmation in the GUI. It copies
exact committed file bytes into `state/staging/PROJECT/…`, executes nothing and
provides no production/network deployment target. Existing releases are never
overwritten. Source archives containing links, credential/runtime directories,
submodules, or export transformations are rejected.

No remote CI credentials, repository secrets, workflows, or production accounts
are configured by this phase. A concrete external runner/target needs separate
configuration and an operation-specific approval design.
