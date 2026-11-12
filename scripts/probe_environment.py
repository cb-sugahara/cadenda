#!/usr/bin/env python3
"""Phase 0: bounded, repeatable CLI probes; no installation or login changes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROBES = [
    ("os", ["sw_vers"]),
    ("arch", ["uname", "-m"]),
    ("memory", ["sysctl", "-n", "hw.memsize"]),
    ("xcode", ["xcode-select", "-p"]),
    ("python", ["python3", "--version"]),
    ("node", ["node", "--version"]),
    ("git", ["git", "--version"]),
    ("brew", ["brew", "--version"]),
    ("beads", ["bd", "--version"]),
    ("beads-init-help", ["bd", "init", "--help"]),
    ("dolt", ["dolt", "version"]),
    ("codex", ["codex", "--version"]),
    ("codex-exec-help", ["codex", "exec", "--help"]),
    ("antigravity", ["agy", "--version"]),
    ("antigravity-help", ["agy", "--help"]),
    ("fm-respond-help", ["fm", "respond", "--help"]),
    ("fm-serve-help", ["fm", "serve", "--help"]),
    ("fm-models", ["fm", "models"]),
    ("fm-quota", ["fm", "quota-usage"]),
]


def probe(name: str, argv: list[str], destination: Path, timeout: int, *, cwd: Path | None = None) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    resolved = shutil.which(argv[0])
    result = {"name": name, "argv": argv, "executable": resolved, "cwd": str(cwd or destination), "started_at": started}
    if not resolved:
        return result | {"status": "missing", "exit_code": None}
    try:
        process = subprocess.Popen(
            argv, cwd=cwd or destination, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            env=os.environ | {"NO_COLOR": "1", "TERM": "dumb", "HOMEBREW_NO_AUTO_UPDATE": "1"},
        )
    except OSError as exc:
        return result | {"status": "launch_error", "exit_code": None, "error": str(exc)}
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    artifacts = {}
    for stream, content in (("stdout", stdout), ("stderr", stderr)):
        clean = ANSI.sub("", content.decode("utf-8", errors="replace")).encode("utf-8")
        path = destination / f"{name}.{stream}.txt"
        path.write_bytes(clean)
        artifacts[stream] = {"path": path.name, "sha256": hashlib.sha256(clean).hexdigest()}
    return result | {
        "status": "timeout" if timed_out else "exited",
        "exit_code": process.returncode,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fm-local", action="store_true", help="Run one synthetic local inference")
    parser.add_argument("--pcc", action="store_true", help="Opt in to one synthetic PCC inference")
    parser.add_argument("--agents", action="store_true", help="Run synthetic Codex/Antigravity JSON probes")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "state" / "probes" / stamp
    destination.mkdir(parents=True, mode=0o700)
    probes = list(PROBES)
    if args.agents:
        prompt = "Do not use tools or read any files. Return a JSON object with status equal to CADENDA_OK. This is a synthetic connectivity test."
        schema = str(ROOT / "schemas" / "smoke-result.schema.json")
        probes.extend([
            ("codex-smoke", ["codex", "exec", "--ignore-user-config", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                "--output-schema", schema, "-o", str(destination / "codex-result.json"), prompt]),
            ("antigravity-smoke", ["agy", "--sandbox", "--mode", "plan", "-p", prompt,
                "--output-format", "json", "--json-schema", schema, "--print-timeout", "40s"]),
        ])
    for enabled, model in ((args.fm_local, "system"), (args.pcc, "pcc")):
        if enabled:
            probes.append((f"fm-{model}-smoke", [
                "fm", "respond", "--model", model, "--no-stream",
                "Reply with the literal text CADENDA_OK. This is a synthetic connectivity test.",
            ]))
    results = []
    for name, argv in probes:
        result = probe(name, argv, destination, timeout=45)
        results.append(result)
        print(f"{name}: {result['status']} exit={result['exit_code']}", flush=True)
    manifest = {"schema_version": 1, "results": results}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Evidence: {destination}")
    print("Exit 0 means capture completed; it does not mean provider compatibility passed.")


if __name__ == "__main__":
    main()
