from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

from .errors import CadendaError
from .security import identifier, safe_relative

ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionReceipt(StrictModel):
    topic: ID
    issue_id: ID
    comment_id: ID
    comment_sha256: SHA
    applies_to: list[ID] = Field(min_length=1)
    approved_at: datetime
    approval_ref: str = Field(min_length=1)

    @field_validator("approved_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Approval time must contain a timezone")
        return value


class Check(StrictModel):
    check_id: ID
    argv: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=300,ge=1,le=3600)


class Project(StrictModel):
    project_id: ID
    root: Path
    worktree_roots: list[Path] = Field(default_factory=list)
    artifact_roots: list[Path] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=lambda: ["AGENTS.md"], max_length=128)
    evidence_catalog: Path | None = None
    decisions: list[DecisionReceipt] = Field(default_factory=list, max_length=64)
    checks: list[Check] = Field(default_factory=list)
    recorder_enabled: bool = False

    @field_validator("root", "worktree_roots", "artifact_roots", "evidence_catalog")
    @classmethod
    def absolute_paths(cls, value):
        values = value if isinstance(value, list) else ([] if value is None else [value])
        for path in values:
            if not path.is_absolute() or path.resolve() != path:
                raise ValueError("Paths must be absolute, canonical paths (no symlinks or traversal)")
        return value

    @field_validator("documents")
    @classmethod
    def relative_documents(cls, value):
        for item in value:
            safe_relative(item)
            if Path(item).suffix.lower() not in {".md", ".txt"}:
                raise ValueError("Documents must be explicit Markdown/text paths; no globs")
        return value

    @model_validator(mode="after")
    def unique_decisions(self):
        seen = set()
        for receipt in self.decisions:
            key = (receipt.topic, receipt.approved_at)
            if key in seen:
                raise ValueError("Decisions of one topic must have distinct approval timestamps")
            seen.add(key)
        return self

    def allowed_worktree(self, path: Path) -> Path:
        resolved = path.resolve(strict=True)
        if path != resolved or not any(resolved.is_relative_to(root) for root in self.worktree_roots):
            raise CadendaError("PATH_DENIED", "Run worktree is outside the project allowlist")
        return resolved

    def artifact_location(self, path: Path) -> tuple[Path, str]:
        # No resolve here: read_bounded rejects every symlink rather than following it.
        if not path.is_absolute():
            raise CadendaError("PATH_DENIED", "Artifacts must use an absolute path")
        for root in sorted(self.artifact_roots, key=lambda p: len(p.parts), reverse=True):
            if path.is_relative_to(root):
                relative = str(path.relative_to(root))
                safe_relative(relative)
                return root, relative
        raise CadendaError("PATH_DENIED", "Artifact is outside the project allowlist")


class Settings(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    state_root: Path
    projects: list[Project]
    max_depth: int = Field(default=2, ge=0, le=8)
    max_issues: int = Field(default=30, ge=1, le=100)
    max_source_bytes: int = Field(default=1_048_576, ge=4096, le=16_777_216)
    budget_tokens: int = Field(default=24000, ge=512, le=1_000_000)
    context_profiles: dict[ID, Annotated[int, Field(ge=512, le=1_000_000)]] = Field(default_factory=dict)

    @field_validator("state_root")
    @classmethod
    def absolute_state(cls, value):
        if not value.is_absolute() or value.resolve() != value:
            raise ValueError("state_root must be absolute and canonical")
        return value

    @model_validator(mode="after")
    def unique_projects(self):
        ids = [project.project_id for project in self.projects]
        roots = [project.root for project in self.projects]
        if len(ids) != len(set(ids)) or len(roots) != len(set(roots)):
            raise ValueError("Project IDs and repository roots must be unique")
        return self


class Registry:
    def __init__(self, settings: Settings):
        self.settings = settings

    @classmethod
    def load(cls, path: Path):
        try:
            if path.stat().st_size > 1_048_576:
                raise CadendaError("CONFIG_INVALID", "Config exceeds 1 MiB")
            return cls(Settings.model_validate(yaml.safe_load(path.read_text())))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise CadendaError("CONFIG_INVALID", "Invalid project configuration; verify schema and canonical absolute paths") from exc

    def project(self, project_id: str) -> Project:
        identifier(project_id)
        for project in self.settings.projects:
            if project.project_id == project_id:
                if not project.root.is_dir() or not (project.root / ".git").exists():
                    raise CadendaError("PROJECT_UNAVAILABLE", "Registered Git repository is unavailable")
                if project.root.resolve() != project.root:
                    raise CadendaError("PATH_DENIED", "Registered root changed to a symlink")
                return project
        raise CadendaError("PROJECT_UNKNOWN", f"Unregistered project: {project_id}")
