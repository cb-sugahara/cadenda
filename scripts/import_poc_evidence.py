#!/usr/bin/env python3
"""Register existing Phase 1 evidence without copying issue bodies or changing Beads.

This owner-run bootstrap binds the prior chat approval to one exact existing comment.
It is NOT an approval endpoint and must not be run by a job on untrusted instructions.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

import yaml

from cadenda.beads_adapter import BeadsAdapter
from cadenda.config import Project, Registry
from cadenda.models import Catalog
from cadenda.security import digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    config_path = ROOT / "config/projects.yaml"
    if config_path.exists():
        raise SystemExit("Local registry already exists; inspect/update explicitly rather than overwrite allowlists")
    state = ROOT / "state/handoff-poc"
    mapping = json.loads((state / "mapping.json").read_text())
    if mapping["approval"]["text"] != "この3子Issueで進める":
        raise SystemExit("Expected prior scoped human approval not found")
    project = Project(project_id="handoff-poc", root=ROOT / "state/projects/handoff-poc",
        worktree_roots=[ROOT / "state/worktrees/handoff-poc"], artifact_roots=[state], documents=["README.md"])
    rows, _ = BeadsAdapter(project).comments(mapping["issues"]["POC-02"])
    decisions = [row for row in rows if str(row.id) == "79161905-be4d-58d2-8b05-ad29e22f4402"]
    if len(decisions) != 1:
        raise SystemExit("Expected PoC decision missing; cannot create receipt")
    decision = decisions[0]
    # The receipt refers to the exact decision originally approved in this chat.
    expected = "[decision_approved] Synthetic PoC requirement change approved by the human's 'この3子Issueで進める' reply to docs/poc-task-proposal.md in the current Codex chat. Current rule: JST Monday 00:00 (Asia/Tokyo UTC+09:00), returned as canonical UTC ISO. This supersedes the POC-01 UTC Monday 00:00 baseline; preserve that historical implementation and evidence. Recorded by Codex as delegated recorder, not as the human actor. No production authorization."
    if decision.text != expected:
        raise SystemExit("Decision content changed; do not approve altered text")
    runs = []
    for number in (1, 2, 3):
        step = json.loads((state / f"step{number}-result.json").read_text())
        pattern = ("fixed-utc-independent-tests", "jst-independent-tests", "http-independent-tests")[number - 1]
        log = sorted((state / "commands").glob(f"*-{pattern}/{pattern}.stdout.txt"))[-1]
        artifacts = [(f"test-{number}", log, "test"),
            (f"review-{number}", state / f"review{number}-result.json", "review"),
            (f"handoff-{number}", state / f"step{number}-result.json", "handoff")]
        if number == 2:
            artifacts.append(("fresh-session-output", state / "jst-handoff-check-result.json", "handoff"))
        runs.append({"run_id": f"poc-run-{number}", "issue_id": step["issue_id"], "provider": "antigravity+codex",
            "outcome": "succeeded", "ended_at": json.loads(sorted((state / "commands").glob(f"*-codex-review-{number}/manifest.json"))[-1].read_text())["ended_at"],
            "commit": step["commit"], "base_commit": step.get("base_commit"), "worktree": step["worktree"],
            "artifacts": [{"artifact_id": key, "path": str(path), "sha256": digest(path.read_bytes()), "kind": kind} for key, path, kind in artifacts]})
    failed_dir = state / "commands/20261112T082205857968Z-utc-implementation"
    failure = json.loads((failed_dir / "manifest.json").read_text())
    runs.append({"run_id": "poc-utc-timeout", "issue_id": mapping["issues"]["POC-01"], "provider": "antigravity",
        "outcome": "interrupted", "ended_at": failure["ended_at"], "artifacts": [
            {"artifact_id": name, "path": str(failed_dir / f"utc-implementation.{name}.txt"),
             "sha256": digest((failed_dir / f"utc-implementation.{name}.txt").read_bytes()), "kind": "log"}
            for name in ("stdout", "stderr")]})
    catalog = Catalog.model_validate({"project_id": project.project_id, "runs": runs})
    catalog_path = state / "evidence-catalog.json"
    catalog_path.write_text(catalog.model_dump_json(indent=2) + "\n")
    configured = project.model_dump(mode="json")
    configured["evidence_catalog"] = str(catalog_path)
    configured["decisions"] = [{"topic": "weekly-boundary", "issue_id": decision.issue_id,
        "comment_id": str(decision.id), "comment_sha256": digest(decision.text.encode()),
        "applies_to": [mapping["issues"]["POC-02"], mapping["issues"]["POC-03"]],
        "approved_at": decision.created_at, "approval_ref": "docs/poc-task-proposal.md; prior Codex chat reply この3子Issueで進める (scoped PoC only)"}]
    payload = {"schema_version": 1, "state_root": str(ROOT / "state"), "projects": [configured]}
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    Registry.load(config_path)
    print(f"Registered existing PoC and 4 execution records: {config_path}")


if __name__ == "__main__":
    main()
