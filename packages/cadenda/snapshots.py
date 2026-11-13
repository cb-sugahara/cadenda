from pathlib import Path
import json
import os
import shutil
import tempfile

from pydantic import ValidationError

from .errors import CadendaError
from .models import Context, Source
from .security import canonical, digest, identifier, read_bounded


class SnapshotStore:
    def __init__(self, state_root: Path):
        self.root = state_root / "contexts"

    def save(self, context: Context, sources: list[Source]):
        from .coordination import access
        if self.root.parent.resolve()!=self.root.parent:raise CadendaError('PATH_DENIED','State root may not traverse symlinks')
        self.root.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with access(self.root.parent,wait=30):
            return self._save(context,sources)

    def _save(self, context: Context, sources: list[Source]):
        if self.root.resolve() != self.root:
            raise CadendaError("PATH_DENIED", "Snapshot root may not traverse symlinks")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise CadendaError("PATH_DENIED", "Snapshot directory may not be a symlink")
        temporary = Path(tempfile.mkdtemp(prefix=".writing-", dir=self.root))
        try:
            files = {"context.json": (context.model_dump_json() + "\n").encode(),
                "context.md": context.markdown().encode()}
            files.update({f"sources/{source.source_id}.json": canonical(source.model_dump(mode="json")) for source in sources})
            for relative, content in files.items():
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            manifest = {"schema_version": 1, "snapshot_id": context.snapshot_id,
                "files": {relative: {"sha256": digest(content), "bytes": len(content)} for relative, content in files.items()}}
            (temporary / "manifest.json").write_bytes(canonical(manifest))
            os.chmod(temporary / "manifest.json", 0o600)
            os.rename(temporary, self.root / context.snapshot_id)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _verified(self, snapshot_id: str):
        identifier(snapshot_id)
        if self.root.resolve() != self.root:
            raise CadendaError("SNAPSHOT_CORRUPT", "Snapshot root changed to a symlink")
        base = self.root / snapshot_id
        try:
            if base.is_symlink():
                raise CadendaError("SNAPSHOT_CORRUPT", "Snapshot directory is a symlink")
            manifest = json.loads(read_bounded(base, "manifest.json", 1_048_576))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
                raise ValueError("Manifest must be an object with a file map")
            if manifest.get("schema_version") != 1 or manifest.get("snapshot_id") != snapshot_id:
                raise ValueError("Manifest identity mismatch")
            if not {"context.json", "context.md"}.issubset(manifest["files"]):
                raise ValueError("Context files missing")
            contents = {}
            for name, metadata in manifest["files"].items():
                if not isinstance(metadata, dict):
                    raise ValueError("Invalid file metadata")
                body = read_bounded(base, name, 32_000_000)
                if digest(body) != metadata["sha256"] or len(body) != metadata["bytes"]:
                    raise ValueError("Checksum mismatch")
                contents[name] = body
            return contents
        except (OSError, ValueError, KeyError, TypeError, CadendaError) as exc:
            raise CadendaError("SNAPSHOT_CORRUPT", "Snapshot is missing, modified, or contains an unsafe path") from exc

    def load(self, snapshot_id: str) -> Context:
        contents = self._verified(snapshot_id)
        try:
            context = Context.model_validate_json(contents["context.json"])
            if context.snapshot_id != snapshot_id:
                raise ValueError("Snapshot identity mismatch")
            if context.calculate_hash() != context.content_sha256:
                raise ValueError("Context content hash mismatch")
            for source in context.sources:
                body = Source.model_validate_json(contents[f"sources/{source.source_id}.json"])
                if body.source_id != source.source_id or digest(canonical(body.data)) != source.content_sha256:
                    raise ValueError("Source integrity mismatch")
            if contents["context.md"].decode() != context.markdown():
                raise ValueError("Rendered context mismatch")
            return context
        except (ValidationError, ValueError, KeyError) as exc:
            raise CadendaError("SNAPSHOT_CORRUPT", "Snapshot schema or source index is invalid") from exc

    def page(self, snapshot_id: str, source_id: str, page: int, page_chars: int = 4000):
        identifier(source_id)
        if page < 0 or not 128 <= page_chars <= 16000:
            raise CadendaError("PAGE_INVALID", "Page must be >=0 and page_chars between 128 and 16000")
        context = self.load(snapshot_id)
        if source_id not in {source.source_id for source in context.sources}:
            raise CadendaError("SOURCE_UNKNOWN", "Source ID is not part of this snapshot")
        contents = self._verified(snapshot_id)
        source = Source.model_validate_json(contents[f"sources/{source_id}.json"])
        text = source.data if isinstance(source.data,str) else json.dumps(source.data, ensure_ascii=False, sort_keys=True, indent=2)
        total = max(1, (len(text) + page_chars - 1) // page_chars)
        if page >= total:
            raise CadendaError("PAGE_INVALID", f"Source has {total} pages (zero based)")
        return {"snapshot_id": snapshot_id, "source_id": source_id, "ref": source.ref,
            "content_sha256": source.content_sha256, "page": page, "pages": total,
            "next_page": page + 1 if page + 1 < total else None,
            "text": text[page * page_chars:(page + 1) * page_chars]}
