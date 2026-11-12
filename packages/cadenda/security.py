"""Bounded local reads and secret removal before persistence or model input."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .errors import CadendaError

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
DENIED_NAMES = {".git", ".beads", ".aws", ".codex", ".ssh", ".gnupg", "keychain", "credentials"}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret|authorization)\s*[:=]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}]+)"),
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]{8,}"),
]


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise CadendaError("INVALID_ID", "IDs must contain only letters, digits, dots, underscores or hyphens and start with a letter/digit")
    return value


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def redact(value):
    """Return redacted JSON-compatible data and the number of substitutions."""
    count = 0

    def walk(item):
        nonlocal count
        if isinstance(item, str):
            item, controls = re.subn(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "[CONTROL]", item)
            count += controls
            for pattern in SECRET_PATTERNS:
                replacement = r"\1[REDACTED]" if pattern.groups else "[REDACTED]"
                item, found = pattern.subn(replacement, item)
                count += found
            return item
        if isinstance(item, dict):
            result = {}
            for key, val in item.items():
                safe_key = walk(str(key))
                candidate, collision = safe_key, 0
                while candidate in result:
                    # Preserve all values when multiple redacted keys become identical.
                    collision += 1
                    candidate = f"{safe_key}[key-{digest(canonical([str(key), collision]))[:12]}]"
                if re.fullmatch(r"(?i)(?:api[_-]?key|access[_-]?token|password|secret|authorization)", str(key)):
                    result[candidate] = "[REDACTED]"
                    count += 1
                else:
                    result[candidate] = walk(val)
            return result
        if isinstance(item, list):
            return [walk(val) for val in item]
        return item

    return walk(value), count


def safe_relative(value: str) -> tuple[str, ...]:
    path = Path(value)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"..", "."} for part in parts):
        raise CadendaError("PATH_DENIED", "Only non-traversing relative paths are allowed")
    if any(part.lower() in DENIED_NAMES or part.lower().startswith(".env") for part in parts):
        raise CadendaError("PATH_DENIED", "Credential and runtime directories are excluded from context")
    return parts


def read_bounded(root: Path, relative: str, limit: int) -> bytes:
    """Open each component without following symlinks (including intermediate dirs)."""
    parts = safe_relative(relative)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CadendaError("PATH_DENIED", "Context sources must be regular files")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise CadendaError("SOURCE_TOO_LARGE", f"Source exceeds {limit} bytes")
            return content
        finally:
            os.close(fd)
    finally:
        os.close(directory)
