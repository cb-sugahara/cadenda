"""aiops: local read-only context CLI. No run/start/update commands in Phase 2."""
import argparse
import json
from pathlib import Path
import sys

from .config import Registry
from .context_builder import ContextBuilder
from .errors import CadendaError
from .snapshots import SnapshotStore
from .security import redact


def parser():
    root = argparse.ArgumentParser(prog="aiops")
    root.add_argument("--config", type=Path, default=Path("config/projects.yaml"))
    commands = root.add_subparsers(dest="command", required=True)
    project = commands.add_parser("projects")
    project.add_argument("--format", choices=["json", "text"], default="text")
    issue = commands.add_parser("issue").add_subparsers(dest="issue_command", required=True)
    build = issue.add_parser("context")
    build.add_argument("project_id")
    build.add_argument("issue_id")
    build.add_argument("--mode", choices=["standard", "debug", "review", "full"], default="standard")
    build.add_argument("--format", choices=["markdown", "json"], default="markdown")
    build.add_argument("--budget-tokens", type=int)
    build.add_argument("--profile", help="Owner-configured context budget profile; does not invoke a model")
    context = commands.add_parser("context").add_subparsers(dest="context_command", required=True)
    show = context.add_parser("show")
    show.add_argument("snapshot_id")
    show.add_argument("--format", choices=["markdown", "json"], default="markdown")
    sources = context.add_parser("sources")
    sources.add_argument("snapshot_id")
    page = context.add_parser("source")
    page.add_argument("snapshot_id")
    page.add_argument("source_id")
    page.add_argument("--page", type=int, default=0)
    page.add_argument("--page-chars", type=int, default=4000)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        registry = Registry.load(args.config)
        store = SnapshotStore(registry.settings.state_root)
        if args.command == "projects":
            rows = [{"project_id": p.project_id, "root": str(p.root)} for p in registry.settings.projects]
            print(json.dumps(rows, ensure_ascii=False, indent=2) if args.format == "json" else "\n".join(f"{p['project_id']}: {p['root']}" for p in rows))
            return 0
        if args.command == "issue":
            context = ContextBuilder(registry).build(args.project_id, args.issue_id, mode=args.mode, budget_tokens=args.budget_tokens, profile=args.profile)
            print(f"Snapshot: {context.snapshot_id}; estimated tokens: {context.estimated_tokens}/{context.budget_tokens}; warnings: {len(context.warnings)}", file=sys.stderr)
        else:
            context = store.load(args.snapshot_id)
            if args.context_command == "sources":
                print(json.dumps([source.model_dump() for source in context.sources], ensure_ascii=False, indent=2))
                return 0
            if args.context_command == "source":
                print(json.dumps(store.page(args.snapshot_id, args.source_id, args.page, args.page_chars), ensure_ascii=False, indent=2))
                return 0
        print(context.model_dump_json() if args.format == "json" else context.markdown(), end="\n" if args.format == "json" else "")
        return 0
    except CadendaError as exc:
        safe, _ = redact(str(exc))
        print(json.dumps({"error": {"code": exc.code, "message": safe}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"error": {"code": "IO_ERROR", "message": "Local data could not be read or persisted"}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
