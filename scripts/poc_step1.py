#!/usr/bin/env python3
"""Run approved POC-01 in its dedicated worktree; independently verify UTC baseline."""
import argparse
import hashlib
import json
from pathlib import Path

from poc_support import STATE, agent, bd, comment, git, mapping, record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-partial", action="store_true", help="Continue preserved files in a fresh session after inspecting failure evidence")
    args = parser.parse_args()
    data = mapping()
    issue = data["issues"]["POC-01"]
    worktree = Path(data["worktree"])
    if (worktree / "src/week-start.ts").exists() and not args.resume_partial:
        raise SystemExit("Implementation already exists; inspect before retrying.")
    info = bd("issue-before", "show", issue)
    bd("claim-first", "update", issue, "--status", "in_progress")
    if args.resume_partial:
        git("preserved-timeout-diff", "diff", cwd=worktree)
        comment(issue, "[run_finished] First UTC implementation attempt timed out at 150s (exit=3, finished_reason=print_timeout). Partial package.json and src/week-start.ts preserved, no success or commit claimed. Original process ended; fresh bounded continuation will finish tests/docs. Evidence: state/handoff-poc/commands/20261112T082205857968Z-utc-implementation. This is observed CLI timeout recovery, not a production orphan-run recovery guarantee.")
    prompt = f"""You implement the currently approved UTC baseline of a synthetic handoff PoC.
Only edit files inside {worktree}. Do not use terminal, shell, network, MCP, or external tools. Do not edit AGENTS.md, .git, or .beads. The deterministic executor will run tests and commit later.
Issue source beads://handoff-poc/{issue}:
{json.dumps(info, ensure_ascii=False)}
Implement ONLY this UTC baseline; the JST change has not yet been issued in this phase.
Create package.json with type=module, scripts.test='node --test', no dependencies; src/week-start.ts exporting weekStart(iso: string): string; test/week-start.test.ts using node:test/assert, README.md documenting accepted ISO datetime syntax and commands.
If package.json or src/week-start.ts already exists from a timed-out session, preserve and finish it; do not re-plan. Keep changes concise. For week arithmetic clone Date then use setUTCHours/setUTCDate; Date.UTC remaps years 0-99 to 1900-1999 and must not be used for those years.
Require a valid timezone-qualified ISO datetime (YYYY-MM-DDTHH:mm:ss with optional fractional seconds and Z or explicit offset). Reject impossible dates such as February 30 and naive timestamps. Output canonical ISO UTC.
Write tests for UTC Sunday/Monday, year boundary, leap dates, invalid input. Node 26 strips TypeScript; use .ts import extensions and no build/dependencies.
Return a structured report. status=implemented only if files were actually edited. Current requirement is UTC Monday 00:00; previous_requirement is empty. Cite Issue source. No tests may be claimed as executed by you.
"""
    result = agent("utc-implementation", prompt, worktree)
    verification = worktree / "verification"
    verification.mkdir(exist_ok=True)
    (verification / "utc.test.ts").write_text('''import { test } from 'node:test';
import assert from 'node:assert/strict';
import { weekStart } from '../src/week-start.ts';
test('independent UTC boundary and year rollover', () => {
  assert.equal(weekStart('2026-11-15T23:59:59Z'), '2026-11-09T00:00:00.000Z');
  assert.equal(weekStart('2026-11-16T00:00:00Z'), '2026-11-16T00:00:00.000Z');
  assert.equal(weekStart('2026-11-16T09:00:00+09:00'), '2026-11-16T00:00:00.000Z');
  assert.equal(weekStart('2027-01-01T01:00:00Z'), '2026-12-28T00:00:00.000Z');
});
test('independent rejection and leap day', () => {
  for (const iso of ['bad', '', '2026-02-30T00:00:00Z', '2026-11-16T00:00:00', '2026-13-01T00:00:00Z']) assert.throws(() => weekStart(iso));
  assert.equal(weekStart('2028-02-29T12:00:00Z'), '2028-02-28T00:00:00.000Z');
});
''')
    test_output = record("utc-independent-tests", ["node", "--test"], cwd=worktree)
    git("stage-utc", "add", "package.json", "src", "test", "verification", "README.md", cwd=worktree)
    git("commit-utc", "-c", "user.name=Cadenda PoC", "-c", "user.email=cadenda@localhost",
        "commit", "-m", "Implement UTC week start baseline with independent tests", cwd=worktree)
    commit = git("utc-head", "rev-parse", "HEAD", cwd=worktree).strip()
    summary = {"issue_id": issue, "commit": commit, "worktree": str(worktree),
        "agent_conversation_id": result["conversation_id"], "tests_sha256": hashlib.sha256(test_output.encode()).hexdigest(),
        "state": "implementation_passed_review_pending", "session_ended": True}
    (STATE / "step1-result.json").write_text(json.dumps(summary, indent=2) + "\n")
    comment(issue, f"[handoff_written] UTC baseline implemented in {commit}. Independent node --test passed. Agent process ended normally; conversation {result['conversation_id']} will NOT be resumed. Evidence: state/handoff-poc/step1-result.json and command manifests. Next: independent Codex review, then approved synthetic JST change. This is an intentional workflow checkpoint, not a process-crash recovery test.")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
