"""aiops: Context inspection and explicit owner-invoked local execution."""
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
    run = commands.add_parser('run').add_subparsers(dest='run_command',required=True)
    start=run.add_parser('start');start.add_argument('project_id');start.add_argument('issue_id');start.add_argument('--checks-only',action='store_true')
    check=run.add_parser('check');check.add_argument('project_id');check.add_argument('issue_id')
    for name in ('show','cancel','release'):
        action=run.add_parser(name);action.add_argument('run_id')
    run.add_parser('recover');run.add_parser('flush')
    review=run.add_parser('review');review.add_argument('run_id')
    pipeline=run.add_parser('pipeline');pipeline.add_argument('project_id');pipeline.add_argument('issue_id');pipeline.add_argument('--max-fixes',type=int,default=2)
    plan=run.add_parser('plan');plan.add_argument('project_id');plan.add_argument('issue_id')
    policy=run.add_parser('policy');policy.add_argument('project_id');policy.add_argument('issue_id')
    local=run.add_parser('local');local.add_argument('project_id');local.add_argument('issue_id');local.add_argument('task',choices=['classify','extract','names','clamp']);local.add_argument('text');local.add_argument('--model',choices=['system','pcc'],default='system');local.add_argument('--classification',choices=['public','internal','secret'],default='internal')
    serve = commands.add_parser("serve");serve.add_argument("--port",type=int,default=8765)
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
        if args.command=="serve":
            from .server import serve
            serve(registry,args.port);return 0
        store = SnapshotStore(registry.settings.state_root)
        if args.command=='run':
            from .recorder import Recorder
            from .worker import Worker, execute
            from .process import capture
            recorder=Recorder(registry.settings.state_root)
            from .pipeline import Pipeline
            if args.run_command=='local':
                from .fm_adapter import LocalService
                result=LocalService(registry).run(args.project_id,args.issue_id,args.task,args.text,model=args.model,classification=args.classification)
            elif args.run_command=='plan': result=Pipeline(registry).architect(args.project_id,args.issue_id)
            elif args.run_command=='policy': result=Pipeline(registry).architect(args.project_id,args.issue_id,policy=True)
            elif args.run_command=='review': result=Pipeline(registry).review(args.run_id)
            elif args.run_command=='pipeline': result=Pipeline(registry).run(args.project_id,args.issue_id,args.max_fixes)
            elif args.run_command=='start': result=Worker(registry).start(args.project_id,args.issue_id,checks_only=args.checks_only)
            elif args.run_command=='show': result=recorder.history(args.run_id)
            elif args.run_command=='cancel': recorder.cancel(args.run_id);result={'cancel_requested':args.run_id}
            elif args.run_command=='recover': result=recorder.recover()
            elif args.run_command=='release': recorder.release(args.run_id);result={'released':args.run_id}
            elif args.run_command=='flush': recorder.flush(registry,capture);result={'outbox':'delivered'}
            else:
                project=registry.project(args.project_id)
                if not project.checks: raise CadendaError('CHECKS_REQUIRED','No owner-approved checks configured')
                from .beads_adapter import BeadsAdapter
                BeadsAdapter(project).issue(args.issue_id)
                key,owner=recorder.claim(args.project_id,args.issue_id,verification=True)
                try:
                    recorder.transition(key,owner,'Running',worktree=str(project.root))
                    recorder.transition(key,owner,'Verifying')
                    for check in project.checks: execute(recorder,key,owner,'check-'+check.check_id,check.argv,project.root,check.timeout_seconds)
                    recorder.transition(key,owner,'Review',pgid=None)
                except BaseException:
                    recorder.transition(key,owner,'NeedsHuman',pgid=None)
                    raise
                result=recorder.get(key)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
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
