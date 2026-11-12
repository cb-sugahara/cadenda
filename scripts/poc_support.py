"""Manual Phase 1 helpers. This is not the production Orchestrator/Context Builder."""
from datetime import datetime, timezone
import json
from pathlib import Path

from probe_environment import ROOT, probe

BASE = ROOT / "state" / "projects" / "handoff-poc"
STATE = ROOT / "state" / "handoff-poc"
MAPPING = STATE / "mapping.json"


def record(name, argv, *, cwd=BASE, timeout=60, required=True, parse=False):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = STATE / "commands" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + name)
    log.mkdir(parents=True, mode=0o700)
    result = probe(name, argv, log, timeout, cwd=cwd)
    (log / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{name}: exit={result['exit_code']} evidence={log.relative_to(ROOT)}", flush=True)
    if required and result["exit_code"] != 0:
        raise RuntimeError(f"{name} failed, inspect {log}")
    text = (log / f"{name}.stdout.txt").read_text()
    if parse:
        return json.loads(text)
    return text


def bd(name, *args):
    return record(name, ["bd", "--directory", str(BASE), *args, "--json"], parse=True)


def comment(issue, text):
    return bd("comment", "comments", "add", issue, text)


def mapping():
    return json.loads(MAPPING.read_text())


def git(name, *args, cwd=BASE):
    return record(name, ["git", "-C", str(cwd), *args], cwd=cwd)


def agent(name, prompt, worktree, *, expected_status="implemented", writable=True):
    settings = {
        "permissions": {
            "allow": [f"read_file({worktree}/)", f"write_file({worktree}/)"],
            "deny": ["command(*)", "read_url(*)", "execute_url(*)", "download_url(*)", "mcp(*)",
                f"write_file({worktree}/.git)", f"write_file({worktree}/.beads/)", f"write_file({worktree}/AGENTS.md)"],
        },
    }
    if not writable:
        settings["permissions"]["deny"].append("write_file(*)")
    settings_path = STATE / f"{name}-settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    prompt_path = STATE / f"{name}-prompt.md"
    prompt_path.write_text(prompt)
    envelope = record(name, ["agy", "--sandbox", "--settings", str(settings_path),
        "--json-schema", str(ROOT / "schemas/poc-agent-result.schema.json"),
        "--output-format", "json", "--effort", "low", "--print-timeout", "300s", "-p", prompt],
        cwd=worktree, timeout=310, parse=True)
    if envelope.get("status") != "SUCCESS" or envelope.get("denied_actions"):
        raise RuntimeError("Agent did not complete cleanly; inspect denial/error evidence")
    structured = envelope.get("structured_output", {})
    if structured.get("status") != expected_status:
        raise RuntimeError("Agent did not report implementation; independent checks still required")
    (STATE / f"{name}-result.json").write_text(json.dumps(envelope, indent=2) + "\n")
    return envelope
