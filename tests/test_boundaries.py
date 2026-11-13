import json
import os
from pathlib import Path
import sys
import time

import pytest
from pydantic import ValidationError

from cadenda.beads_adapter import BeadsAdapter
from cadenda.config import Project, Registry, Settings
from cadenda.errors import CadendaError
from cadenda.process import capture
from cadenda.security import read_bounded
from conftest import row


def test_registry_rejects_unknown_and_duplicate_projects(registry):
    with pytest.raises(CadendaError, match="Unregistered"):
        registry.project("other")
    with pytest.raises(ValidationError):
        Settings(state_root=registry.settings.state_root, projects=registry.settings.projects * 2)
    with pytest.raises(ValidationError):
        Project(project_id="relative", root=Path("relative"))


def test_config_safe_yaml_and_extra_keys(tmp_path):
    config = tmp_path / "bad.yaml"
    sentinel = tmp_path / "should-not-exist"
    config.write_text(f'!!python/object/apply:os.system ["touch {sentinel}"]')
    with pytest.raises(CadendaError) as exc:
        Registry.load(config)
    assert exc.value.code == "CONFIG_INVALID"
    assert not sentinel.exists()


def test_no_traversal_symlinks_or_secret_paths(registry, tmp_path):
    root = registry.settings.projects[0].root
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("must not read")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "linked.txt").symlink_to(outside / "secret.txt")
    for name in ("../outside/secret.txt", ".env", ".aws/credentials", "escape/secret.txt", "linked.txt"):
        with pytest.raises((CadendaError, OSError)):
            read_bounded(root, name, 1000)


def test_replaced_project_root_denied(registry, tmp_path):
    root = registry.settings.projects[0].root
    backup = tmp_path / "backup"
    root.rename(backup)
    root.symlink_to(backup, target_is_directory=True)
    with pytest.raises(CadendaError) as exc:
        registry.project("test")
    assert exc.value.code == "PATH_DENIED"


def test_beads_is_readonly_and_id_is_not_a_flag(registry):
    calls = []

    def runner(argv, cwd):
        calls.append(argv)
        return 0, json.dumps([row()]), ""

    adapter = BeadsAdapter(registry.project("test"), runner=runner)
    adapter.issue("bd-1")
    assert "--readonly" in calls[0] and "--sandbox" in calls[0]
    assert "--id=bd-1" in calls[0]
    for value in ("--global", "../secret", "bd-1\n", "a; touch foo"):
        with pytest.raises(CadendaError):
            adapter.issue(value)
    assert len(calls) == 1


def test_duplicate_normalized_comment_ids_are_rejected(registry):
    base = {"issue_id": "bd-1", "author": "actor", "created_at": "2026-11-12T00:00:00Z"}
    raw = [base | {"id": 1, "text": "original"}, base | {"id": "1", "text": "replacement"}]
    adapter = BeadsAdapter(registry.project("test"), runner=lambda *args: (0, json.dumps(raw), ""))
    with pytest.raises(CadendaError) as exc:
        adapter.comments("bd-1")
    assert exc.value.code == "SCHEMA_MISMATCH"


@pytest.mark.parametrize("result,code", [
    ((0, "broken", ""), "INVALID_JSON"),
    ((0, "", '{"unsupported": true, "error": "unsupported embedded"}'), "UNSUPPORTED"),
    ((1, "", "issue not found"), "NOT_FOUND"),
    ((1, "", "operation not permitted"), "PERMISSION"),
    ((1, "", "authentication required"), "AUTHENTICATION"),
    ((0, "[]", ""), "NOT_FOUND"),
    ((0, '{"id":"other","title":"x","status":"open"}', ""), "SCHEMA_MISMATCH"),
])
def test_beads_classifies_errors_including_zero_exit(registry, result, code):
    adapter = BeadsAdapter(registry.project("test"), runner=lambda *args: result)
    with pytest.raises(CadendaError) as exc:
        adapter.issue("bd-1")
    assert exc.value.code == code


@pytest.mark.parametrize("status", [[], {"unexpected": "object"}])
def test_malformed_status_is_classified_not_a_typeerror(registry, status):
    adapter = BeadsAdapter(registry.project("test"), runner=lambda *args: (0, json.dumps({"status": status}), ""))
    with pytest.raises(CadendaError) as exc:
        adapter.issue("bd-1")
    assert exc.value.code == "SCHEMA_MISMATCH"


def test_process_output_and_timeout_limits(tmp_path):
    with pytest.raises(CadendaError) as exc:
        capture([sys.executable, "-c", "print('x'*100000)"], tmp_path, max_bytes=1000)
    assert exc.value.code == "OUTPUT_LIMIT"
    started = time.monotonic()
    with pytest.raises(CadendaError) as exc:
        capture([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.15)
    assert exc.value.code == "TIMEOUT"
    assert time.monotonic() - started < 5


def test_timeout_when_parent_exits_but_child_holds_pipe(tmp_path):
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])"
    started = time.monotonic()
    with pytest.raises(CadendaError) as exc:
        capture([sys.executable, "-c", code], tmp_path, timeout=0.2)
    assert exc.value.code == "TIMEOUT"
    assert time.monotonic() - started < 5


def test_capture_default_environment_does_not_inherit_secrets(tmp_path,monkeypatch):
    import sys
    from cadenda.process import capture
    monkeypatch.setenv('CADENDA_TEST_PRIVATE','synthetic-do-not-forward')
    code,out,err=capture([sys.executable,'-c','import os;print("CADENDA_TEST_PRIVATE" in os.environ)'],tmp_path)
    assert code==0 and out.strip()=='False'
    code,out,err=capture([sys.executable,'-c','import os;print(os.environ["EXPLICIT_FLAG"])'],tmp_path,env={'EXPLICIT_FLAG':'owned'})
    assert code==0 and out.strip()=='owned'


def test_beads_full_edges_preserve_dependency_direction(registry):
    edge={'issue_id':'bd-1','depends_on_id':'bd-0','type':'blocks'}
    dependent={'issue_id':'bd-2','depends_on_id':'bd-1','type':'blocks'}
    payload=row(dependencies=[edge],dependents=[dependent])
    adapter=BeadsAdapter(registry.project('test'),runner=lambda *args:(0,json.dumps([payload]),''))
    issue,raw=adapter.issue('bd-1')
    assert issue.dependencies[0].id=='bd-0' and issue.dependencies[0].dependency_type=='blocks'
    assert issue.dependents[0].id=='bd-2' and issue.dependents[0].dependency_type=='blocks'
    assert raw[0]['dependencies']==[edge]
