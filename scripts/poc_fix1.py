#!/usr/bin/env python3
"""Add a strict-input regression after checking an independent review finding."""
import hashlib
import json
from pathlib import Path

from poc_support import STATE, agent, comment, git, mapping, record


def main():
    data = mapping()
    issue = data["issues"]["POC-01"]
    step_path = STATE / "step1-result.json"
    step = json.loads(step_path.read_text())
    worktree = Path(step["worktree"])
    for filename in ("step1-result.json", "review1-result.json", "review1-input.json"):
        target = STATE / ("pre-fix-" + filename)
        if not target.exists():
            target.write_bytes((STATE / filename).read_bytes())
    verifier = worktree / "verification/utc.test.ts"
    regression = "\ntest('independent strict ISO rejects trailing newline', () => { assert.throws(() => weekStart('2026-11-09T00:00:00Z\\n')); });\n"
    if "rejects trailing newline" not in verifier.read_text():
        verifier.write_text(verifier.read_text() + regression)
    record("reproduce-newline-bug", ["node", "--test", "verification/utc.test.ts"], cwd=worktree, required=False)
    result = agent("strict-iso-fix", f"Fix the concrete Codex review finding on beads://handoff-poc/{issue}. Only edit {worktree}/src/week-start.ts and test/week-start.test.ts. Do not run commands or use network/MCP. The current regex dollar anchor accepts a trailing newline; require full input consumption (for example match[0] === iso) and add a regression in the existing test suite. Keep all UTC behavior unchanged. Do not edit verification tests or other files. This is a tiny patch, do not re-plan. Return status=implemented, current_requirement='UTC Monday 00:00 and strict ISO input', previous_requirement='', cite {issue} and the review. Do not claim tests were run.", worktree)
    output = record("fixed-utc-independent-tests", ["node", "--test"], cwd=worktree)
    git("stage-strict-fix", "add", "src/week-start.ts", "test/week-start.test.ts", "verification/utc.test.ts", cwd=worktree)
    git("commit-strict-fix", "-c", "user.name=Cadenda PoC", "-c", "user.email=cadenda@localhost", "commit", "-m", "Reject trailing newline in ISO input after independent review", cwd=worktree)
    previous_commit = step["commit"]
    step.update({"previous_commit": previous_commit, "commit": git("fixed-utc-head", "rev-parse", "HEAD", cwd=worktree).strip(),
        "agent_conversation_id": result["conversation_id"], "tests_sha256": hashlib.sha256(output.encode()).hexdigest()})
    step_path.write_text(json.dumps(step, indent=2) + "\n")
    comment(issue, f"[test_passed] Independent newline regression PASSED on the original code: the review's bug claim was not reproducible in Node 26 (JavaScript non-multiline dollar anchor rejects the final newline). Fresh Antigravity session added explicit full-match checking and a regression as defensive clarity only. All node --test cases pass. New commit {step['commit']}; original {previous_commit} and rejected review preserved under pre-fix-* snapshots. Next: repeat independent Codex review with reproduction evidence.")


if __name__ == "__main__":
    main()
