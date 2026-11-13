import React from 'react';
import {pipelineState} from './pipeline-state.js';
const statusLabels={complete:'完了',running:'実行中',waiting:'待機',stopped:'確認が必要',skipped:'対象外',rejected:'却下済み'};
export default function PipelineStepper({history}) {
 if(!history)return <p className="muted">実行中のタスクはありません。Issueボードから仕事を選んでください。</p>;
 const steps=pipelineState(history);
 return <section className="pipeline-progress" aria-label="タスクの進行状況">
  <p><b>{history.run.project_id} / {history.run.issue_id}</b> · {history.run.state}</p>
  <ol>{steps.map(step=><li key={step.label} className={step.status} aria-current={step.status==='running'?'step':undefined}><b>{step.label}</b><span>{step.actor}</span><small>{statusLabels[step.status]}</small></li>)}</ol>
  {history.run.state==='NeedsHuman'&&<p className="warning">自動再実行はしません。ログを確認して、停止・再開を判断してください。</p>}
 </section>;
}
