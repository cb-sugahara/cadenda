export function initialProject(data) {
 return data.runs.find(r=>r.active)?.project_id||data.runs[0]?.project_id||data.projects[0]?.project_id||'';
}
export function selectCurrentRun(runs,project) {
 return runs.find(r=>r.active)||runs.find(r=>r.project_id===project);
}
export function pipelineState(history) {
 const {run,events=[]}=history;
 const implementation=run.provider==='antigravity+executor';
 const local=run.provider?.startsWith('fm');
 const started=events.filter(e=>e.event_type==='command_started');
 const completed=prefix=>events.some(e=>e.event_type==='command_finished'&&e.payload.name?.startsWith(prefix)&&e.payload.exit_code===0&&!e.payload.reason);
 const reviewIndex=events.findLastIndex(e=>e.event_type==='review_started');
 const reviewed=reviewIndex>=0;
 const reviewedComplete=reviewed&&events.slice(reviewIndex+1).some(e=>e.event_type==='state_changed'&&e.payload.to==='Review')&&events.slice(reviewIndex+1).some(e=>e.event_type==='command_finished'&&e.payload.name?.startsWith('reviewer-')&&e.payload.exit_code===0&&!e.payload.reason);
 const human=events.filter(e=>e.event_type==='human_review_decided').at(-1);
 let current=implementation?0:1;
 if(started.some(e=>e.payload.name?.startsWith('check-')))current=1;
 if(reviewed||implementation&&run.state==='Review')current=2;
 if(run.state==='Review'&&(!implementation||reviewedComplete))current=3;
 if(local)current=run.state==='Review'?3:0;
 const stopped=['NeedsHuman','Interrupted','RetryableFailure','Abandoned'].includes(run.state);
 const steps=[
  {label:local?'ローカル処理':'実装',actor:local?'fm':'Antigravity',skipped:!implementation&&!local,done:local?run.state==='Review'&&events.some(e=>e.event_type==='command_finished'&&e.payload.exit_code===0&&!e.payload.reason):completed('agent')},
  {label:'テスト',actor:'Executor',skipped:local,done:completed('check-')&&(current>1||run.state==='Review')},
  {label:'レビュー',actor:'Codex',skipped:!implementation,done:reviewedComplete&&run.state==='Review'},
  {label:'人間確認',actor:'あなた',skipped:false,done:human?.payload.verdict==='approve',rejected:human?.payload.verdict==='reject'}
 ];
 return steps.map((step,i)=>({...step,status:step.skipped?'skipped':step.rejected?'rejected':i===current&&stopped?'stopped':step.done?'complete':i===current?(run.active?'running':'waiting'):'waiting'}));
}
