#!/usr/bin/env python3
"""Phase 0 CLI contract test in a new disposable repository, never production."""
from datetime import datetime, timezone
import json
from pathlib import Path

from probe_environment import ROOT, probe


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "state" / "beads-smoke" / stamp
    destination.mkdir(parents=True, mode=0o700)
    results = []

    def run(name: str, argv: list[str], *, required: bool = True, parse_json: bool = True):
        result = probe(name, argv, destination, timeout=45)
        results.append(result)
        (destination / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "results": results,
        }, ensure_ascii=False, indent=2) + "\n")
        print(f"{name}: {result['status']} exit={result['exit_code']}", flush=True)
        if required and result["exit_code"] != 0:
            raise RuntimeError(f"{name} failed; inspect {destination}")
        if parse_json and result["exit_code"] == 0 and "--json" in argv:
            return json.loads((destination / f"{name}.stdout.txt").read_text())

    print(f"Evidence / test repository: {destination}", flush=True)
    run("git-init", ["git", "init", "--initial-branch=main"])
    run("init", ["bd", "init", "--prefix", "probe", "--non-interactive", "--skip-hooks", "--skip-agents"])
    run("engine", ["bd", "dolt", "status", "--json"])
    # bd 1.3.4 embedded doctor emits unsupported=true to stderr with exit 0.
    run("doctor", ["bd", "doctor", "--json"], required=False, parse_json=False)
    # These are CLI fixtures, not the Phase 1 development task decomposition.
    parent = run("create-epic", ["bd", "create", "Phase 0 CLI fixture (not development work)",
        "--type", "epic", "--description", "Disposable CLI contract checks only.", "--json"])["id"]
    first = run("create-first", ["bd", "create", "CLI fixture: prerequisite",
        "--parent", parent, "--description", "Verify show, comment and close JSON contracts.",
        "--acceptance", "Comment can be read; closed issue disappears from ready.", "--json"])["id"]
    second = run("create-second", ["bd", "create", "CLI fixture: dependent",
        "--parent", parent, "--description", "Must be blocked until prerequisite closes.",
        "--acceptance", "Ready includes this issue only after prerequisite closes.", "--json"])["id"]
    run("dependency", ["bd", "dep", "add", second, first, "--json"])
    comment = "[comment] Phase 0 synthetic fixture; no specification approval is claimed."
    run("add-comment", ["bd", "comments", "add", first, comment, "--json"])
    comments = run("read-comments", ["bd", "comments", first, "--json"])
    assert any(item.get("text") == comment for item in comments), comments
    ready_before = run("ready-before", ["bd", "ready", "--json"])
    assert first in {item["id"] for item in ready_before}, ready_before
    assert second not in {item["id"] for item in ready_before}, ready_before
    run("show-first", ["bd", "show", first, "--json"])
    run("close-first", ["bd", "close", first, "--reason", "CLI fixture assertions passed; not product completion.", "--json"])
    ready_after = run("ready-after", ["bd", "ready", "--json"])
    assert first not in {item["id"] for item in ready_after}, ready_after
    assert second in {item["id"] for item in ready_after}, ready_after
    run("close-second", ["bd", "close", second, "--reason", "Dependency readiness assertion passed.", "--json"])
    run("close-epic", ["bd", "close", parent, "--reason", "CLI contract fixture complete.", "--json"])
    summary = {"schema_version": 1, "status": "passed", "epic": parent,
        "prerequisite": first, "dependent": second, "repository": str(destination)}
    (destination / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
