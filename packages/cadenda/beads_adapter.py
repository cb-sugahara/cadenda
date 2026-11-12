import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .config import Project
from .errors import CadendaError
from .process import capture
from .security import identifier, redact


class Link(BaseModel):
    id: str
    dependency_type: str = "related"


class Issue(BaseModel):
    id: str
    title: str
    description: str = ""
    acceptance_criteria: str = ""
    status: str
    revision: str | None = None
    parent: str | None = None
    dependencies: list[Link] = Field(default_factory=list)
    dependents: list[Link] = Field(default_factory=list)
    comment_count: int | None = None


class Comment(BaseModel):
    id: str | int
    issue_id: str
    author: str
    text: str
    created_at: str


class BeadsAdapter:
    """Only these read operations are exposed; no generic command passthrough."""

    def __init__(self, project: Project, *, runner=capture, executable="bd"):
        self.project = project
        self.runner = runner
        self.executable = executable

    def _json(self, *args) -> Any:
        argv = [self.executable, "--readonly", "--sandbox", "--directory", str(self.project.root), *args, "--json"]
        code, out, err = self.runner(argv, self.project.root)
        payload = None
        try:
            payload = json.loads(out)
        except (json.JSONDecodeError, TypeError):
            pass
        # Some bd commands emit structured errors on stderr with exit 0.
        try:
            error_payload = json.loads(err)
        except (json.JSONDecodeError, TypeError):
            error_payload = None
        failure = next((item for item in (payload, error_payload) if isinstance(item, dict) and
            (item.get("unsupported") or item.get("error") or
             (isinstance(item.get("status"), str) and item["status"] in {"ERROR", "INVALID"}))), None)
        if code != 0 or failure:
            detail = json.dumps(failure) if failure else err or out
            lower = detail.lower()
            category = "COMMAND_FAILED"
            for markers, kind in [
                (("unsupported", "not supported"), "UNSUPPORTED"),
                (("not found", "no issue", "no issues found", "not exist"), "NOT_FOUND"),
                (("permission", "operation not permitted", "access denied"), "PERMISSION"),
                (("authentication", "unauthorized", "login required"), "AUTHENTICATION"),
            ]:
                if any(marker in lower for marker in markers):
                    category = kind
                    break
            safe, _ = redact(detail)
            raise CadendaError(category, f"Beads read failed: {safe[:500]}")
        if payload is None:
            raise CadendaError("INVALID_JSON", "Beads did not return valid JSON")
        return payload

    def issue(self, issue_id: str) -> tuple[Issue, Any]:
        identifier(issue_id)
        raw = self._json("show", f"--id={issue_id}", "--brief-deps", "--include-dependents")
        rows = raw if isinstance(raw, list) else [raw]
        if not rows:
            raise CadendaError("NOT_FOUND", f"Issue not found: {issue_id}")
        try:
            if len(rows) != 1:
                raise ValueError("Expected exactly one issue")
            issue = Issue.model_validate(rows[0])
            if issue.id != issue_id:
                raise ValueError("Issue ID mismatch")
        except (ValidationError, ValueError) as exc:
            raise CadendaError("SCHEMA_MISMATCH", "Unexpected Beads issue shape") from exc
        return issue, raw

    def comments(self, issue_id: str) -> tuple[list[Comment], Any]:
        identifier(issue_id)
        raw = self._json("comments", issue_id)
        try:
            if not isinstance(raw, list):
                raise ValueError("Expected a comment list")
            comments = [Comment.model_validate(item) for item in raw]
            if any(comment.issue_id != issue_id for comment in comments):
                raise ValueError("Comment issue ID mismatch")
            ids = [str(comment.id) for comment in comments]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate normalized comment IDs")
        except (ValidationError, ValueError) as exc:
            raise CadendaError("SCHEMA_MISMATCH", "Unexpected Beads comments shape") from exc
        return comments, raw
