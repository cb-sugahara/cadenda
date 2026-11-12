#!/usr/bin/env python3
"""Finalize the Phase 1 test only after all child review/evidence gates pass."""
import hashlib
import json
from pathlib import Path

from poc_support import ROOT, STATE, bd, comment, git, mapping


def main():
    if (STATE / "final-result.json").exists():
        raise SystemExit("PoC was already finalized; inspect existing results rather than duplicate Issue events.")
    data = mapping()
    steps = []
    for n in (1, 2, 3):
        step = json.loads((STATE / f"step{n}-result.json").read_text())
        review = json.loads((STATE / f"review{n}-result.json").read_text())
        issue = bd(f"final-child-{n}", "show", data["issues"][f"POC-{n:02}"])
        row = issue[0] if isinstance(issue, list) else issue
        if row["status"] != "closed" or review["verdict"] != "approved":
            raise SystemExit(f"Child {n} is not reviewed and closed")
        if not step.get("session_ended"):
            raise SystemExit(f"Child {n} agent session is still active")
        if git(f"final-head-{n}", "rev-parse", "HEAD", cwd=Path(step["worktree"])).strip() != step["commit"]:
            raise SystemExit(f"Child {n} revision differs from reviewed evidence")
        if git(f"final-clean-{n}", "status", "--porcelain", cwd=Path(step["worktree"])).strip():
            raise SystemExit(f"Child {n} worktree is dirty")
        steps.append(step)
    handoff = json.loads((STATE / "jst-handoff-check-result.json").read_text())
    if handoff["conversation_id"] == steps[0]["agent_conversation_id"]:
        raise SystemExit("Handoff reused implementation conversation")
    comment(data["epic"], f"[handoff_written] Phase 1 initial gate passed. All 3 approved children have independent Node test evidence and separate Codex approved reviews. Fresh handoff session {handoff['conversation_id']} reconstructed current JST versus previous UTC requirement, constraints and next actions without prior conversation. UTC, JST and HTTP commits: {', '.join(step['commit'] for step in steps)}. Sources: state/handoff-poc/jst-context.md; step1/2/3-result.json; review1/2/3-result.json; command manifests. Initial timeout and false-positive-review correction remain in child 1 history. Worktrees preserved; no daemon/deployment started. Next: Phase 2 Project Registry / BeadsAdapter / Context Builder. Crash/orphan-run recovery and GUI are not claimed.")
    bd("close-reviewed-epic", "close", data["epic"], "--reason", "Approved 3-child PoC completed with fresh-session handoff, independent tests and Codex reviews; all worktrees/evidence retained. Phase 2 remains future work.")
    export = ROOT / "docs/evidence/poc"
    export.mkdir(parents=True, exist_ok=True)
    names = ["mapping.json", "jst-context.json", "jst-context.md", "jst-handoff-check-result.json", "pre-fix-review1-result.json"]
    for n in (1, 2, 3):
        names += [f"step{n}-result.json", f"review{n}-result.json"]
    index_path = ROOT / "docs/evidence/index.json"
    index = json.loads(index_path.read_text())
    known_exports = {item["export"] for item in index["files"]}
    for name in names:
        source = STATE / name
        content = source.read_bytes()
        target = export / name
        target.write_bytes(content)
        rel = str(target.relative_to(ROOT))
        entry = {"source": str(source.relative_to(ROOT)), "export": rel, "sha256": hashlib.sha256(content).hexdigest()}
        if rel not in known_exports:
            index["files"].append(entry)
    for n, step in enumerate(steps, start=1):
        # Export the actual test runner output, not an AI's summary of it.
        pattern = "fixed-utc-independent-tests" if n == 1 else ("jst-independent-tests" if n == 2 else "http-independent-tests")
        sources = sorted((STATE / "commands").glob(f"*-{pattern}/{pattern}.stdout.txt"))
        source = sources[-1]
        target = export / f"step{n}-tests.txt"
        content = source.read_bytes()
        target.write_bytes(content)
        index["files"].append({"source": str(source.relative_to(ROOT)), "export": str(target.relative_to(ROOT)), "sha256": hashlib.sha256(content).hexdigest()})
    summary = {"schema_version": 1, "phase0": "passed_with_documented_pcc_and_doctor_limits", "phase1": "passed",
        "epic": data["epic"], "steps": steps, "next_phase": 2}
    (STATE / "final-result.json").write_text(json.dumps(summary, indent=2) + "\n")
    (export / "final-result.json").write_text(json.dumps(summary, indent=2) + "\n")
    content = (export / "final-result.json").read_bytes()
    index["files"].append({"source": "state/handoff-poc/final-result.json", "export": "docs/evidence/poc/final-result.json", "sha256": hashlib.sha256(content).hexdigest()})
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
