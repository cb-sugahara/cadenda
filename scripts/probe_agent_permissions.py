#!/usr/bin/env python3
"""Phase 0: exercise scoped write, headless Ask soft-deny and CLI timeout."""
from datetime import datetime, timezone
import json

from probe_environment import ROOT, probe


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "state" / "permissions-probes" / stamp
    destination.mkdir(parents=True, mode=0o700)
    allowed = destination / "allowed.txt"
    guarded = destination / "approval-required.txt"
    settings = {"permissions": {
        "allow": [f"write_file({allowed})"],
        "ask": [f"write_file({guarded})"],
        "deny": ["command(*)", "read_url(*)", "execute_url(*)", "download_url(*)", "mcp(*)"],
    }}
    (destination / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    results = []
    scenarios = [
        ("allowed-write", f"Use the file editing tool to create exactly {allowed} with content CADENDA_ALLOWED. Do not use shell commands, read other files, or change anything else. If denied stop and report denial. Do not seek alternate ways to write."),
        ("ask-write", f"Use the file editing tool to create exactly {guarded} with content CADENDA_GUARDED. Do not use shell commands, read other files, or change anything else. If denied stop and report denial. Do not seek alternate ways to write."),
    ]
    print(f"Evidence: {destination}", flush=True)
    for name, prompt in scenarios:
        result = probe(name, ["agy", "--sandbox", "--mode", "auto",
            "--settings", str(destination / "settings.json"), "-p", prompt,
            "--output-format", "json", "--print-timeout", "40s"], destination, 45)
        results.append(result)
        (destination / "manifest.json").write_text(json.dumps({"schema_version": 1, "results": results}, indent=2) + "\n")
        print(f"{name}: {result['status']} exit={result['exit_code']}", flush=True)
    timeout = probe("cli-timeout", ["agy", "--sandbox", "--mode", "plan", "-p",
        "Do not use tools. Reply with CADENDA_OK.", "--output-format", "json",
        "--print-timeout", "1ms"], destination, 20)
    results.append(timeout)
    (destination / "manifest.json").write_text(json.dumps({"schema_version": 1, "results": results}, indent=2) + "\n")
    ask_result = json.loads((destination / "ask-write.stdout.txt").read_text())
    timeout_result = json.loads((destination / "cli-timeout.stdout.txt").read_text())
    summary = {
        "schema_version": 1,
        "allowed_file_correct": allowed.is_file() and allowed.read_text().strip() == "CADENDA_ALLOWED",
        "guarded_file_absent": not guarded.exists(),
        "soft_deny_notice": "denied" in (destination / "ask-write.stderr.txt").read_text().lower(),
        "denied_actions_recorded": any(item.get("action") == "write_file" for item in ask_result.get("denied_actions", [])),
        "timeout_exit_code": timeout["exit_code"],
        "timeout_capture_status": timeout["status"],
        "timeout_reported": timeout["exit_code"] != 0 and timeout_result.get("finished_reason") == "print_timeout",
    }
    (destination / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    # These independent filesystem checks are necessary even when agy exits 0.
    if not all(summary[key] for key in ("allowed_file_correct", "guarded_file_absent", "soft_deny_notice", "denied_actions_recorded", "timeout_reported")):
        raise SystemExit("Permission contract not established; inspect evidence, do not enable worker writes.")


if __name__ == "__main__":
    main()
