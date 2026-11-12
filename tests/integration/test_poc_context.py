from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from cadenda.config import Registry
from cadenda.context_builder import ContextBuilder
from cadenda.snapshots import SnapshotStore


@pytest.mark.integration
def test_real_poc_all_modes_and_replay(request):
    config = request.config.getoption("--poc-config")
    if config is None:
        pytest.skip("Real PoC is opt in: --poc-config config/projects.yaml")
    registry = Registry.load(Path(config))
    store = SnapshotStore(registry.settings.state_root)
    snapshots = []
    for mode in ("standard", "debug", "review", "full"):
        context = ContextBuilder(registry).build("handoff-poc", "poc-eye.3", mode=mode)
        snapshots.append(context.snapshot_id)
        approved = [s for s in context.sections if s.title.startswith("Current approved requirements")]
        assert len(approved) == 1 and "Current rule: JST Monday 00:00" in approved[0].text
        assert "No production authorization" in approved[0].text
        assert any(s.ref.endswith("/79161905-be4d-58d2-8b05-ad29e22f4402") for s in context.sources)
        assert not {w.code for w in context.warnings} - {"SOURCE_EXCERPT", "BUDGET_TRUNCATED"}
        assert context.estimated_tokens <= context.budget_tokens
        assert store.load(context.snapshot_id).markdown() == context.markdown()
        test_source = next(s for s in context.sources if s.ref.endswith("poc-run-3/test-3"))
        result = store.page(context.snapshot_id, test_source.source_id, 0, 16000)
        assert "tests 44" in result["text"] and "fail 0" in result["text"]
        if mode == "standard":
            assert any(s.title.startswith("Latest recorded handoff") for s in context.sections)
            assert "tests 44" in context.markdown()
        if mode == "debug":
            assert "print timeout" in context.markdown() or "print_timeout" in context.markdown()
    assert len(set(snapshots)) == 4
    # Fresh OS process, no bd/git on PATH: replay must read only the frozen snapshot.
    result = subprocess.run([sys.executable, "-m", "cadenda.cli", "--config", str(Path(config).resolve()),
        "context", "show", snapshots[-1], "--format", "json"],
        env=os.environ | {"PATH": "/nonexistent"}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["snapshot_id"] == snapshots[-1]
