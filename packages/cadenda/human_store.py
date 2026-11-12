"""Authenticated decision receipts; Beads remains the requirement/comment authority."""
import json
from contextlib import closing
from .config import DecisionReceipt
from .security import canonical,digest


def issue_fingerprint(issue):
    return digest(canonical(issue.model_dump(exclude={'revision','comment_count'})))


def initialize(recorder):
    with recorder.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS human_receipts(project_id TEXT NOT NULL, receipt TEXT NOT NULL, actor_ref TEXT NOT NULL, PRIMARY KEY(project_id, receipt))')
        db.execute('CREATE TABLE IF NOT EXISTS gui_jobs(job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, issue_id TEXT NOT NULL, prior_run_id TEXT, worker_pid INTEGER, replacement_run_id TEXT, state TEXT NOT NULL, created_at REAL NOT NULL, exit_code INTEGER, output_path TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS owner_audit(id INTEGER PRIMARY KEY, occurred_at REAL NOT NULL, actor_ref TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL)')


def receipts(recorder, project_id):
    with closing(recorder.connect()) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='human_receipts'").fetchone()
        if not exists:return []
        return [DecisionReceipt.model_validate_json(r[0]) for r in db.execute('SELECT receipt FROM human_receipts WHERE project_id=?',(project_id,))]
