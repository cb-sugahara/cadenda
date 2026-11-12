from pathlib import Path

import pytest

from cadenda.config import Project, Registry, Settings
from cadenda.beads_adapter import Comment, Issue
from cadenda.errors import CadendaError


def row(key="bd-1", **changes):
    return {"id": key, "title": "Task " + key, "description": "Implement the requirement.",
        "acceptance_criteria": "Independent test passes", "status": "open", "revision": "1", **changes}


def comment(key, text, issue_id="bd-1"):
    return {"id": key, "issue_id": issue_id, "author": "untrusted-agent", "text": text, "created_at": "2026-11-12T00:00:00Z"}


class FakeBeads:
    def __init__(self, issues=None, comments=None):
        self.rows = issues or {"bd-1": row()}
        self.messages = comments or {}
        self.fetched = []

    def issue(self, key):
        self.fetched.append(key)
        if key not in self.rows:
            raise CadendaError("NOT_FOUND", "Missing fixture issue")
        return Issue.model_validate(self.rows[key]), [self.rows[key]]

    def comments(self, key):
        raw = self.messages.get(key, [])
        return [Comment.model_validate(value) for value in raw], raw


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    worktrees, artifacts = tmp_path / "worktrees", tmp_path / "artifacts"
    worktrees.mkdir()
    artifacts.mkdir()
    project = Project(project_id="test", root=root.resolve(), worktree_roots=[worktrees.resolve()],
        artifact_roots=[artifacts.resolve()], documents=[])
    return Registry(Settings(state_root=(tmp_path / "state").resolve(), projects=[project], budget_tokens=100000))


def git_runner(argv, cwd, **kwargs):
    if "rev-parse" in argv:
        return 0, "a" * 40 + "\n", ""
    if "status" in argv:
        return 0, "", ""
    return 0, "diff --git a/file b/file\n+safe change\n", ""


def pytest_addoption(parser):
    parser.addoption("--poc-config", default=None, help="Explicit owner-controlled local config for real Beads integration")
