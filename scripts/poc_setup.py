#!/usr/bin/env python3
"""Create the user-approved Phase 1 issues in a new local test repository."""
from datetime import datetime, timezone
import json

from poc_support import BASE, STATE, MAPPING, bd, comment, git, record


def main():
    if BASE.exists() or MAPPING.exists():
        raise SystemExit("PoC already exists; inspect mapping and resume rather than recreating issues.")
    BASE.mkdir(parents=True, mode=0o700)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    git("init", "init", "--initial-branch=main")
    git("initial-commit", "-c", "user.name=Cadenda PoC", "-c", "user.email=cadenda@localhost",
        "commit", "--allow-empty", "-m", "Initialize isolated handoff PoC")
    record("beads-init", ["bd", "init", "--prefix", "poc", "--non-interactive", "--skip-agents", "--skip-hooks"])
    epic = bd("epic", "create", "Phase 1: 週間ランキング週開始APIとセッション引継ぎ", "--type", "epic",
        "--description", "User approved docs/poc-task-proposal.md in this Codex chat on 2026-11-12: この3子Issueで進める. Local synthetic PoC only; no deployment.",
        "--acceptance", "Fresh session recovers current requirements and evidence from Beads without prior conversation; independent tests and Codex review pass.")["id"]
    tasks = [
        ("POC-01", "UTC週開始計算と境界テスト", "Implement src/week-start.ts exporting weekStart(iso: string): string. UTC Monday 00:00 baseline. Require timezone-qualified valid ISO datetime; reject invalid calendar dates and naive input. Node built-in TypeScript stripping and node:test only; no dependencies.", "Independent tests: Sunday/Monday, year boundary, invalid dates, no-offset input. Commit in dedicated worktree."),
        ("POC-02", "停止後のJST仕様変更と新セッション引継ぎ", "After POC-01 process ends, record the user-approved synthetic change: JST Monday 00:00 instead of UTC Monday. Start a new agent session using only Beads context and Git evidence; no --continue or conversation resume. Implement updated weekStart and update tests.", "Fresh session explicitly identifies old UTC requirement and current approved JST requirement. Boundary 2026-11-15T14:59:59Z -> 2026-11-08T15:00:00.000Z; 2026-11-15T15:00:00Z -> same instant. Independent tests pass."),
        ("POC-03", "ローカルHTTP APIと独立レビュー", "Add src/server.ts exporting createServer() for GET /week-start?at=<ISO>. Valid input returns {week_start: ISO UTC string}; invalid ->400; unknown route ->404. Entry point binds only 127.0.0.1. Use node:http and node:test; no third-party packages. Review separately with Codex.", "HTTP tests exercise 200/400/404; JST boundary preserved. Read-only Codex review compares acceptance criteria, diff, tests, source references. No production deployment."),
    ]
    result = {"schema_version": 1, "project_id": "handoff-poc", "epic": epic, "issues": {},
        "approval": {"received_at": datetime.now(timezone.utc).isoformat(), "source": "Current Codex chat: request_user_input_async reply", "text": "この3子Issueで進める", "proposal": "docs/poc-task-proposal.md"}}
    # Persist IDs immediately; partial setup must not silently duplicate on retry.
    MAPPING.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    previous = None
    for key, title, description, criteria in tasks:
        issue = bd(key, "create", title, "--parent", epic, "--description", description,
            "--acceptance", criteria, "--priority", "1")["id"]
        result["issues"][key] = issue
        MAPPING.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        if previous:
            bd("dependency", "dep", "add", issue, previous)
        previous = issue
    comment(epic, "[approved-plan] Human response in current Codex chat: この3子Issueで進める. Scope: docs/poc-task-proposal.md (3 children, dependency chain, synthetic UTC-to-JST change, local worktree implementation/tests). Recorded by Codex, not independent identity authentication. Production approval remains out of scope.")
    first = result["issues"]["POC-01"]
    worktree = BASE.parents[1] / "worktrees" / "handoff-poc" / first
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "worktree", "add", "-b", f"ai/{first}", str(worktree), "HEAD")
    result["worktree"] = str(worktree)
    MAPPING.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
