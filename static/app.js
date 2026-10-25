'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label=v=>String(v??'').replaceAll('_',' ');
const fmt=new Intl.NumberFormat(undefined,{maximumFractionDigits:1});
const time=v=>v?new Date(v).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Unknown';
const relative=v=>{if(!v)return'Unknown';const n=(new Date(v)-Date.now())/60000;return n<0?'Due / awaiting sample':n<60?`${Math.ceil(n)} min`:`${fmt.format(n/60)} h`;};
const empty=(a,b='')=>`<div class="empty"><strong>${esc(a)}</strong>${esc(b)}</div>`;
const pill=(value,style='')=>`<span class="status ${style}">${esc(label(value))}</span>`;
const kv=(a,b)=>`<div class="kv"><span>${esc(a)}</span><span>${b}</span></div>`;
const thread=(id,text='Open thread')=>/^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$/.test(id||'')?`<a href="t3-thread://v1/${esc(id)}">${esc(text)}</a>`:'—';
const active=new Set(['stop_requested','preparing','prepared','launching','running','usage_limited','waiting','review_pending','incomplete','attention','recovery_pending','replacement_ready','replacement_launching']);
let state=null;
let submitting=false;
function launchControl(j){
 if(!['queued','preparing','prepared'].includes(j.state))return '';
 return `<button type="button" data-launch="${esc(j.id)}" ${submitting?'disabled':''}>${j.launchApproval?'Retry launch':'Launch'}</button>`;
}
function workerControl(j){
 if(j.state==='stopped')return `<button type="button" data-worker-action="resume" data-job="${esc(j.id)}" ${submitting?'disabled':''}>Resume</button>`;
 if(j.state==='stop_requested')return `<button type="button" data-worker-action="stop" data-job="${esc(j.id)}" ${submitting?'disabled':''}>Retry stop</button>`;
 if(j.stopAvailable)return `<button type="button" data-worker-action="stop" data-job="${esc(j.id)}" title="Interrupt this worker and prevent automatic restart until you click Resume" ${submitting?'disabled':''}>Stop</button>`;
 return '';
}
function controlProgress(j,decision){
 if(j.state==='stop_requested')return 'Stop requested; automatic restart is blocked. '+(j.workerControl?.error||j.workerControl?.notification?.error||'Waiting for the watcher to interrupt the worker and confirm it has stopped.');
 if(j.state==='stopped')return 'Stopped by you. The watcher will leave this worker stopped until you click Resume.';
 if(j.state==='waiting'&&j.observation?.operatorResumeRequested)return 'Resume requested. '+(decision?.reason||'Waiting for admission checks.');
 return '';
}
function workerSettings(j){
 const options=j.request.selection.options||{};
 const settings=Object.entries(options).map(([key,value])=>['reasoningEffort','effort'].includes(key)?(value==='xhigh'?'Extra high':label(value)).replace(/^./,c=>c.toUpperCase())+' reasoning':key==='serviceTier'?(value==='default'?'Standard tier':label(value)+' tier'):label(key)+': '+value);
 settings.push(j.request.subagents==='none'?'No subagents':j.request.subagents==='integration'?'Integration agent only':'Up to '+j.request.subagentCap+' subagents');
 return settings.join(' · ');
}
async function operatorAction(path,payload){
 if(submitting||!state)return;
 submitting=true;renderJobs();$('launch-mode').disabled=true;
 try{
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrfToken},body:JSON.stringify(payload)});
  const result=await r.json();if(!r.ok)throw new Error(result.error||'Action failed');
  await refresh();
  if(result.control){
   const notice=result.control.notification;
   $('action-status').textContent=result.control.phase==='resumed'?'Resume requested. The watcher will use the same thread when admission checks pass.':result.job.state==='stopped'?'Worker is stopped. The watcher will leave it stopped until Resume.':'Stop requested; automatic restart is blocked. '+(notice?.state==='sent'?'Watcher notified; waiting for T3 interruption.':notice?.error||'Waiting to notify the watcher.');
  }else $('action-status').textContent=result.approval?(result.notification?.sent?'Launch requested; watcher notified.':'Launch saved. '+(result.notification?.reason||'Waiting for the watcher.')):'Launch mode: '+result.launchMode+'.';
 }catch(e){$('action-status').textContent=e.message;}
 finally{submitting=false;renderJobs();$('launch-mode').value=state.plan.launchMode;$('launch-mode').disabled=false;}
}

function renderJobs(){
 if(!state)return;
 const filter=$('filter').value;
 const jobs=state.jobs.filter(j=>filter==='all'||(filter==='active'?active.has(j.state):filter==='attention'?['attention','incomplete','review_pending'].includes(j.state):j.state===filter));
 const rows=jobs.map(j=>{
  const decision=state.plan.decisions.find(d=>d.jobId===j.id);
  const progress=controlProgress(j,decision)||(j.state==='queued'&&decision?.reason)||j.observation?.stage||j.observation?.summary||j.error||'Waiting for the watcher';
  const preview=progress.length>220?progress.slice(0,217)+'…':progress;
  const browser=j.observation?.browserVerified?`<a href="http://127.0.0.1:${Number(j.port)}" target="_blank" rel="noreferrer">Browser :${Number(j.port)}</a>`:'';
  return `<tr><td class="device">${j.thread_id?thread(j.thread_id,j.name):`<strong>${esc(j.name)}</strong>`}</td><td>${pill(j.state,['attention','incomplete','usage_limited'].includes(j.state)?'warn':'')}</td><td class="profile">${esc(j.request.selection.model)}${j.modelEditable?` <button type="button" data-edit-model="${esc(j.id)}">Edit</button>`:''}<small>${esc(workerSettings(j))}</small></td><td class="progress"><span title="${esc(progress)}">${esc(preview)}</span><small>${esc(time(j.observation?.observedAt||j.updated))}</small></td><td class="actions">${launchControl(j)}${workerControl(j)}${browser}</td></tr>`;
 });
 $('jobs').innerHTML=jobs.length?`<div class="table-wrap"><table class="jobs-table"><thead><tr><th>Device</th><th>Status</th><th>Worker</th><th>Progress</th><th>Actions</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`:empty(state.jobs.length?'No matching jobs.':'No jobs queued.',state.jobs.length?'Choose another filter.':'Add firmware with re-launch.');
}
function renderAccounts(){
 const usage=state.usage;
 const stale=!state.plan.quotaFresh;
 $('sample-time').textContent=usage?`Updated ${time(usage.time)}${stale?' · stale or incomplete':''}`:'No samples yet';
 if(!usage?.accounts?.length){$('accounts').innerHTML=empty('Usage unavailable.',usage?.error||'Waiting for a usage sample.');return;}
 const accountDetailsOpen=document.getElementById('account-details')?.open;
 const rows=usage.accounts.map(a=>{
  const forecasts=state.forecasts.filter(f=>f.account===a.account);
  const count=a.resets?.available_count;
  const windows=a.windows.length?a.windows:[null];
  return `<tbody>${windows.map((w,i)=>{
   const account=i===0?`<th scope="rowgroup" class="account-name" rowspan="${windows.length}">${esc(a.provider==='claude'?'Claude':'Codex')}<small>${esc(a.account)}${a.disabled?' · disabled':''}</small>${count!=null?`<small>Reset vouchers: ${esc(count)}</small>`:''}</th>`:'';
   if(!w)return `<tr>${account}<td colspan="4" class="warn-text">Usage unknown${a.errors.length?': '+esc(a.errors.join('; ')):''}</td></tr>`;
   const prediction=forecasts.find(f=>f.window===w.window);
   const name=w.seconds===18000?'5-hour':w.seconds===604800?'Weekly':label(w.window);
   const scope=w.scope&&w.scope!=='all'?' · '+label(w.scope).replace(/^(seven day|five hour)\s*/, '').replace(/:$/, ''):'';
   return `<tr>${account}<td>${esc(name+scope)}</td><td class="${w.used>=98?'warn-text':''}">${fmt.format(w.used)}%</td><td>${esc(relative(w.reset_at))}${w.reset_at?`<small>${esc(time(w.reset_at))}</small>`:''}</td><td>${prediction?.ratePerHour!=null?fmt.format(prediction.ratePerHour)+' pp/h':'Measuring'}${prediction?.exhaustHours!=null?`<small>Limit in ~${fmt.format(prediction.exhaustHours)} h</small>`:''}${a.errors.length?`<small class="warn-text">${esc(a.errors.join('; '))}</small>`:''}</td></tr>`;
  }).join('')}</tbody>`;
 }).join('');
 const details=usage.accounts.map(a=>{
  const grants=a.resets?.grants||a.resets?.credits||[];
  const changes=state.forecasts.filter(f=>f.account===a.account&&f.resetEvent);
  return `<div class="review"><strong>${esc(a.account)}</strong><p>Reset vouchers: ${a.resets?.available_count==null?'unknown':esc(a.resets.available_count)}</p>${grants.map(g=>`<p>${esc(g.label||g.status||'Grant')} · expires ${esc(time(g.ends_at||g.expires_at||g.expiresAt))}${g.usable_now?' · usable now':''}</p>`).join('')}${changes.map(f=>`<p>${esc(label(f.window))}: ${esc(f.resetEvent.kind)} · ${fmt.format(f.resetEvent.drop)} pp decrease</p>`).join('')}</div>`;
 }).join('');
 $('accounts').innerHTML=`<div class="table-wrap"><table class="usage-table"><thead><tr><th>Account</th><th>Window</th><th>Used</th><th>Reset</th><th>Rate</th></tr></thead>${rows}</table></div><details id="account-details" class="subsection" ${accountDetailsOpen?'open':''}><summary>Reset and account details</summary><div class="details-content">${details}<p class="note">Account windows stay separate. Rotation and voucher redemption are not automated.</p></div></details>`;
}
function renderChart(){
 const pick=$('window-filter').value;const rows=state.history.filter(r=>pick==='all'||(pick==='short'?r.seconds!=null&&r.seconds<=86400:r.seconds!=null&&r.seconds>86400));
 if(!rows.length){$('chart').innerHTML=empty('No history for this window','Usage measurements will appear here as samples accumulate.');$('legend').innerHTML='';return;}
 const groups=new Map();for(const r of rows){const key=r.account+' / '+label(r.window);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
 const colors=['#7798bb','#8e9b83','#b19b7b','#9c91ab','#819fa2','#ad8c8c','#9a9a9a','#a1a77a'];
 const start=Math.min(...rows.map(r=>Date.parse(r.time)));const end=Math.max(Date.now(),start+60000);const x=t=>48+(t-start)/(end-start)*960;const y=u=>190-u*1.65;
 let svg=`<svg viewBox="0 0 1040 230" role="img" aria-label="Quota usage by account and window over the last 24 hours">`;
 for(const u of [0,25,50,75,100])svg+=`<line class="grid" x1="48" x2="1008" y1="${y(u)}" y2="${y(u)}"/><text x="8" y="${y(u)+3}">${u}%</text>`;
 let legend='';let i=0;
 for(const [name,points] of groups){const color=colors[i++%colors.length];let segments=[[]];for(const p of points){let segment=segments.at(-1);if(segment.length&&Date.parse(p.time)-Date.parse(segment.at(-1).time)>15*60000){segment=[];segments.push(segment);}segment.push(p);}
 for(const ps of segments){svg+=`<polyline fill="none" stroke="${color}" stroke-width="2" points="${ps.map(p=>`${x(Date.parse(p.time))},${y(p.used)}`).join(' ')}"/>`;for(const p of ps)svg+=`<circle cx="${x(Date.parse(p.time))}" cy="${y(p.used)}" r="2" fill="${color}"><title>${esc(name)} · ${esc(time(p.time))} · ${p.used}%</title></circle>`;}
 legend+=`<span class="legend-item"><i class="swatch" style="background:${color}"></i>${esc(name)}</span>`;}
 svg+=`<text x="48" y="220">${esc(time(start))}</text><text x="1008" text-anchor="end" y="220">${esc(time(end))}</text></svg>`;$('chart').innerHTML=svg;$('legend').innerHTML=legend;
}
function renderTokens(){
 const t=state.tokens;
 $('tokens').innerHTML=t?['claude','codex'].map(p=>{const v=t[p];const n=v?.report?.totals;return `<div class="token-row"><h3>${esc(p==='claude'?'Claude':'Codex')}</h3>${v?.ok&&n?`<div class="token-number">${fmt.format(n.totalTokens??0)} <span class="muted small">reported tokens</span></div>${kv('Input / output',esc(fmt.format(n.inputTokens??0))+' / '+esc(fmt.format(n.outputTokens??0)))}${kv('Cache read / write',esc(fmt.format(n.cacheReadTokens??0))+' / '+esc(fmt.format(n.cacheCreationTokens??0)))}<p class="muted small">${v.report.sessions?.length??0} local sessions · cumulative report, not a sum of polls</p>`:empty('Unknown',v?.error||'No local token report')}</div>`;}).join('')+`<p class="note">Sample ${esc(time(t.time))}. Cache fields follow each CLI’s definitions. Local logs can be incomplete; tokens do not represent remaining subscription quota.</p>`:empty('No local token report yet','ccusage reads available Claude and Codex session logs.');
}
let recoveryDirty=false;
let recoveryRevision=null;
let recoverySaving=false;
let ruleNumber=0;

function notificationText(notice){
 if(!notice)return 'No watcher update has been sent for these rules.';
 if(notice.state==='sent')return 'Rules saved. Update sent to the watcher '+time(notice.sentAt)+'. It queues if the watcher is busy.';
 if(notice.state==='pending')return 'Rules saved. '+(notice.error||'Waiting to notify the watcher.');
 if(notice.state==='sending')return 'Rules saved. Notification delivery is not yet confirmed.';
 if(notice.state==='uncertain')return 'Rules saved. '+(notice.error||'Notification delivery could not be confirmed.');
 return 'Rules saved.';
}
function setRepairFields(row){
 const repair=row.querySelector('[data-field=action]').value==='repair_then_replace';
 row.querySelector('.repair-fields').hidden=!repair;
 row.querySelectorAll('.repair-fields input, .repair-fields textarea').forEach(e=>e.disabled=!repair);
}
function appendRecoveryRule(rule={}){
 let id=rule.id;
 if(!id){do{id='rule-'+(++ruleNumber);}while([...$('recovery-rules').querySelectorAll('[data-field=id]')].some(e=>e.value===id));}
 const row=document.createElement('fieldset');row.className='rule-editor';row.dataset.rule='';
 row.innerHTML=`<div class="rule-heading"><label>Rule ID <input data-field="id" value="${esc(id)}" pattern="[a-z0-9]([a-z0-9_]|-){0,63}" maxlength="64" required></label><button type="button" data-remove-rule>Remove rule</button></div>
 <label>When should this count as a broken thread?<textarea data-field="when" required>${esc(rule.when||'')}</textarea></label>
 <label>Action <select data-field="action"><option value="replace">Replace thread</option><option value="repair_then_replace">Repair, then replace if unsuccessful</option></select></label>
 <div class="repair-fields">
  <label>Repair steps (one per line)<textarea data-field="repairSteps" required>${esc((rule.repairSteps||[]).join('\n'))}</textarea></label>
  <label>How to verify the repair worked<textarea data-field="verify" required>${esc(rule.verify||'')}</textarea></label>
  <label>Maximum repair attempts <input data-field="maxRepairAttempts" type="number" min="1" max="5" value="${Number(rule.maxRepairAttempts)||1}" required></label>
 </div>`;
 row.querySelector('[data-field=action]').value=rule.action||'replace';
 $('recovery-rules').append(row);setRepairFields(row);
}
function loadRecoveryEditor(snapshot){
 const policy=snapshot.policy;
 $('recovery-enabled').checked=policy.enabled;
 $('recovery-max').value=policy.maxReplacementsPerJob;
 $('recovery-cooldown').value=policy.cooldownSeconds;
 $('recovery-rules').replaceChildren();ruleNumber=0;
 for(const rule of policy.rules)appendRecoveryRule(rule);
 recoveryRevision=snapshot.revision;recoveryDirty=false;
 $('recovery-fields').disabled=false;
 $('recovery-save-status').textContent=notificationText(snapshot.notification);
 $('retry-policy-notification').hidden=!['pending','sending','uncertain'].includes(snapshot.notification?.state);
}
function renderRecoveryEditor(){
 if(!state.recoveryPolicyRevision)return;
 const snapshot={policy:state.recoveryPolicy,revision:state.recoveryPolicyRevision,notification:state.recoveryPolicyNotification};
 if(!recoveryDirty&&!recoverySaving){
  if(recoveryRevision!==snapshot.revision)loadRecoveryEditor(snapshot);
  else{
   $('recovery-save-status').textContent=notificationText(snapshot.notification);
   $('retry-policy-notification').hidden=!['pending','sending','uncertain'].includes(snapshot.notification?.state);
  }
 }else if(recoveryDirty&&recoveryRevision!==snapshot.revision){
  $('recovery-save-status').textContent='Saved rules changed elsewhere. Your draft is retained; reload the saved rules before saving.';
 }
}
function dirtyRecoveryEditor(){
 recoveryDirty=true;$('recovery-save-status').textContent='Unsaved changes.';
 $('retry-policy-notification').hidden=true;
}
function recoveryDraft(){
 return {enabled:$('recovery-enabled').checked,maxReplacementsPerJob:Number($('recovery-max').value),cooldownSeconds:Number($('recovery-cooldown').value),rules:[...$('recovery-rules').querySelectorAll('[data-rule]')].map(row=>{
  const field=name=>row.querySelector(`[data-field=${name}]`).value;
  const repair=field('action')==='repair_then_replace';
  return {id:field('id').trim(),when:field('when').trim(),action:field('action'),repairSteps:repair?field('repairSteps').split('\n').map(s=>s.trim()).filter(Boolean):[],verify:repair?field('verify').trim():'',maxRepairAttempts:repair?Number(field('maxRepairAttempts')):0};
 })};
}
async function saveRecoveryPolicy(retry=false){
 if(recoverySaving||!state?.recoveryPolicyRevision)return;
 if(retry&&recoveryDirty){$('recovery-save-status').textContent='Save your draft before notifying the watcher.';return;}
 recoverySaving=true;$('recovery-fields').disabled=true;
 $('recovery-save-status').textContent=retry?'Notifying watcher…':'Saving rules and notifying watcher…';
 try{
  const payload={expectedRevision:recoveryRevision};
  if(!retry)payload.policy=recoveryDraft();
  const response=await fetch(retry?'/api/recovery-policy/notify':'/api/recovery-policy',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrfToken},body:JSON.stringify(payload)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Policy update failed.');
  state.recoveryPolicy=result.policy;state.recoveryPolicyRevision=result.revision;state.recoveryPolicyNotification=result.notification;
  loadRecoveryEditor(result);$('recovery-state').textContent=result.policy.enabled?'enabled':'disabled';
 }catch(error){$('recovery-save-status').textContent=error.message+' Your draft has been retained.';}
 finally{recoverySaving=false;$('recovery-fields').disabled=false;}
}
function renderHostUsage(){
 const host=state.hostUsage;
 if(!host){$('host-usage').textContent='Factory host: CPU and RAM readings unavailable.';return;}
 const cpu=host.cpuPercent==null?((host.errors||[]).some(e=>e.includes('CPU'))?'unavailable':'measuring'):fmt.format(host.cpuPercent)+'%';
 const ram=host.memoryUsedBytes==null?'unavailable':fmt.format(host.memoryUsedBytes/1073741824)+' / '+fmt.format(host.memoryTotalBytes/1073741824)+' GiB ('+fmt.format(host.memoryPercent)+'%)';
 $('host-usage').textContent='Factory host · CPU '+cpu+' · RAM '+ram;
 $('host-usage').title=(host.errors||[]).join(' ')||(host.cpuCount+' logical CPUs. CPU is averaged between samples; RAM excludes available cache.');
}

function render(data){
 state=data;$('launch-mode').value=data.plan.launchMode;$('launch-mode-note').textContent=data.plan.launchMode==='manual'?'Click Launch to approve each new worker.':'Queued jobs start when usage and capacity allow.';$('error').hidden=true;$('sync').textContent='Updated '+new Date(data.time).toLocaleTimeString();
 const stats=[['active',data.jobs.filter(j=>active.has(j.state)).length],['queued',data.plan.queued],['completed',data.jobs.filter(j=>j.state==='verified_complete').length],['need attention',data.jobs.filter(j=>['attention','incomplete','usage_limited'].includes(j.state)).length]];
 $('stats').innerHTML=stats.map(([name,count])=>`<span><strong>${count}</strong> ${name}</span>`).join('');
 $('watch-state').textContent=data.config.watcherMessagesPaused?'messages paused':label(data.watcher.phase);
 $('watcher').innerHTML=kv('Thread',thread(data.watcher.thread))+kv('Ownership generation',esc(data.watcher.epoch))+kv('Last heartbeat',esc(time(data.watcher.heartbeat)))+kv('Wake interval',esc(data.config.wakeSeconds??'—')+' seconds');
 $('policy').textContent=label(data.plan.policy);
 const next=data.plan.decisions.find(d=>d.admit)||data.plan.decisions[0];
 $('admission').innerHTML=next?`<p>${esc(next.name)} ${pill(next.admit?'eligible to start':'hold',next.admit?'':'warn')}</p><p class="muted">${esc(next.reason)}</p>`:empty('No pending launch decisions','Add firmware groups with re-launch.');
 $('admission').innerHTML+=`<p class="note">Usage ${data.plan.quotaFresh?'fresh':'missing or stale'}. Maximum ${Number(data.plan.maxParallel)} concurrent workers.</p>`;
 renderJobs();checkModelEditor();renderAccounts();renderChart();renderTokens();renderHostUsage();renderRecoveryEditor();
 $('recovery-state').textContent=data.recoveryPolicy?.enabled?'enabled':'disabled';
 $('recoveries').innerHTML=data.recoveries?.length?data.recoveries.slice(0,12).map(r=>`<div class="review"><strong>${esc(data.jobs.find(j=>j.id===r.job)?.name||r.job)}</strong> ${pill(r.phase,['attention','launching'].includes(r.phase)?'warn':'neutral')}<p>Rule: ${esc(r.rule_id)} · repair attempts: ${Number(r.repair_attempts)} · ${esc(time(r.updated))}</p>${r.error?`<p class="warn-text">${esc(r.error)}</p>`:''}</div>`).join(''):empty('No thread recovery incidents',data.recoveryPolicy?.enabled?'The watcher uses your saved criteria and bounded repair procedure.':'Waiting for your broken-thread criteria and repair instructions.');
 const reviews=data.jobs.filter(j=>j.review||j.state==='review_pending');$('reviews').innerHTML=reviews.length?reviews.map(j=>`<div class="review"><strong>${esc(j.name)}</strong> ${pill(j.review?.verdict||'review pending')}<p>${esc(j.review?.summary||'Independent audit pending.')}</p>${(j.review?.findings||[]).map(f=>`<p class="warn-text">${esc(typeof f==='object'?JSON.stringify(f):f)}</p>`).join('')}<p>${j.review?.commit?'Commit '+esc(j.review.commit.slice(0,12)):''} ${thread(j.thread_id)}</p></div>`).join(''):empty('No completion claims yet','Completed workers need source, evidence, checks and browser verification before acceptance.');
 const openedEvents=new Set([...document.querySelectorAll('#events details[open]')].map(e=>e.dataset.event));
 $('events').innerHTML=data.events.length?data.events.slice(0,35).map(e=>`<details class="event" data-event="${esc(e.id)}" ${openedEvents.has(String(e.id))?'open':''}><summary><time>${esc(time(e.time))}</time><span>${esc(label(e.kind))}</span></summary><pre>${esc(e.detail)}</pre></details>`).join(''):empty('No activity yet.');

}
async function refresh(){try{const r=await fetch('/api/status');if(!r.ok)throw new Error('Dashboard request failed: HTTP '+r.status);render(await r.json());if(modelEdit)await loadModelCatalog();return true;}catch(e){$('error').hidden=false;$('error').textContent=e.message+' Displayed data may be stale.';$('sync').textContent='Connection lost';return false;}}
$('jobs').addEventListener('click',e=>{const button=e.target.closest('[data-launch]');if(button)operatorAction('/api/jobs/'+button.dataset.launch+'/launch',{});});
$('launch-mode').addEventListener('change',e=>operatorAction('/api/launch-mode',{mode:e.target.value}));
$('filter').addEventListener('change',renderJobs);$('window-filter').addEventListener('change',()=>state&&renderChart());refresh();setInterval(refresh,15000);

$('recovery-form').addEventListener('submit',e=>{e.preventDefault();saveRecoveryPolicy();});
$('recovery-form').addEventListener('input',dirtyRecoveryEditor);
$('recovery-form').addEventListener('change',e=>{if(e.target.matches('[data-field=action]'))setRepairFields(e.target.closest('[data-rule]'));dirtyRecoveryEditor();});
$('add-recovery-rule').addEventListener('click',()=>{appendRecoveryRule();dirtyRecoveryEditor();});
$('recovery-rules').addEventListener('click',e=>{if(e.target.closest('[data-remove-rule]')){e.target.closest('[data-rule]').remove();dirtyRecoveryEditor();}});
$('reload-recovery-policy').addEventListener('click',async()=>{if(await refresh())loadRecoveryEditor({policy:state.recoveryPolicy,revision:state.recoveryPolicyRevision,notification:state.recoveryPolicyNotification});});
$('retry-policy-notification').addEventListener('click',()=>saveRecoveryPolicy(true));

let modelEdit=null;
let modelCatalog=null;
let modelSaving=false;
function selectedProvider(){return modelCatalog?.providers.find(p=>p.providerInstanceId===$('model-provider').value);}
function selectedModel(){return selectedProvider()?.models.find(m=>m.id===$('model-name').value);}
function drawModelOptions(saved={}){
 const descriptors=selectedModel()?.options||[];
 $('model-options').innerHTML=descriptors.map((d,i)=>{
  const value=Object.hasOwn(saved,d.id)?saved[d.id]:(d.currentValue??(d.type==='boolean'?false:d.options.find(o=>o.isDefault)?.id??d.options[0]?.id));
  return `<label>${esc(d.label)} ${d.type==='boolean'?`<input type="checkbox" data-model-option="${esc(d.id)}" ${value?'checked':''}>`:`<select data-model-option="${esc(d.id)}" required>${d.options.map(o=>`<option value="${esc(o.id)}" ${o.id===value?'selected':''}>${esc(o.label||o.id)}</option>`).join('')}</select>`}</label>`;
 }).join('');
 checkModelEditor();
}
function drawModels(saved){
 const models=selectedProvider()?.models||[];
 $('model-name').innerHTML=models.map(m=>`<option value="${esc(m.id)}">${esc(m.label||m.id)}</option>`).join('');
 if(saved){
  if(!models.some(m=>m.id===saved.model))$('model-name').insertAdjacentHTML('beforeend',`<option value="${esc(saved.model)}">${esc(saved.model)} (unavailable)</option>`);
  $('model-name').value=saved.model;
 }
 drawModelOptions(saved?.options);
}
function drawProviders(saved){
 $('model-provider').innerHTML=(modelCatalog?.providers||[]).map(p=>`<option value="${esc(p.providerInstanceId)}">${esc(p.displayName||p.providerInstanceId)}</option>`).join('');
 if(saved){
  if(!modelCatalog?.providers.some(p=>p.providerInstanceId===saved.instanceId))$('model-provider').insertAdjacentHTML('beforeend',`<option value="${esc(saved.instanceId)}">${esc(saved.instanceId)} (unavailable)</option>`);
  $('model-provider').value=saved.instanceId;
 }
 drawModels(saved);
}
async function modelPost(path,payload){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrfToken},body:JSON.stringify(payload)});
 const result=await response.json();if(!response.ok)throw new Error(result.error||'Worker settings update failed.');return result;
}
function showCatalogStatus(){
 if(!modelCatalog)return;
 $('model-catalog-status').textContent=(modelCatalog.fetchedAt?'T3 catalog updated '+time(modelCatalog.fetchedAt):'No T3 catalog loaded')+(modelCatalog.stale?' · stale':'')+(modelCatalog.refreshPending?' · refresh requested; waiting for watcher':'')+'.';
}
async function loadModelCatalog(){
 const response=await fetch('/api/models');if(!response.ok)throw new Error('Model catalog unavailable.');
 const next=await response.json();
 if(modelEdit&&modelCatalog&&modelCatalog.fetchedAt!==next.fetchedAt&&!modelSaving){
  const saved={instanceId:$('model-provider').value,model:$('model-name').value,options:{}};
  for(const input of $('model-options').querySelectorAll('[data-model-option]'))saved.options[input.dataset.modelOption]=input.type==='checkbox'?input.checked:input.value;
  modelCatalog=next;drawProviders(saved);checkModelEditor();
 }else modelCatalog=next;
 showCatalogStatus();
}
async function openModelEditor(id){
 if(modelSaving)return;
 const job=state.jobs.find(j=>j.id===id);if(!job?.modelEditable)return;
 modelEdit={id,revision:job.modelRevision};$('model-editor').hidden=false;
 $('model-editor-title').textContent='Worker settings · '+job.name;
 $('model-status').textContent='Loading T3 models…';$('model-fields').disabled=true;
 try{
  $('worker-subagents').value=job.request.subagents;
  $('worker-subagent-cap').value=job.request.subagentCap||1;
  syncSubagentControls();
  $('model-pool').value=job.request.quotaPool;
  await loadModelCatalog();drawProviders(job.request.selection);
  $('model-status').textContent=selectedModel()?'': 'The saved model is unavailable. Subagent edits keep it unchanged; choose an available model or refresh to change the model.';
 }catch(error){drawProviders(job.request.selection);$('model-status').textContent=error.message+' The saved model is retained; subagent settings can still be changed.';}
 finally{$('model-fields').disabled=false;checkModelEditor();}
}
function checkModelEditor(){
 if(!modelEdit||modelSaving)return;
 const job=state.jobs.find(j=>j.id===modelEdit.id);
 const locked=!job?.modelEditable;
 $('model-save').disabled=locked||job.modelRevision!==modelEdit.revision;
 if(locked)$('model-status').textContent='This job has begun launching. Worker settings editing is closed.';
 else if(job.modelRevision!==modelEdit.revision)$('model-status').textContent='Saved settings changed elsewhere. Your draft is retained; reload saved settings before saving.';
}
$('jobs').addEventListener('click',e=>{const button=e.target.closest('[data-edit-model]');if(button)openModelEditor(button.dataset.editModel);});
$('model-provider').addEventListener('change',()=>{drawModels();$('model-pool').value=($('model-name').value.startsWith('claude')||$('model-provider').value==='claudeAgent')?'claude':'codex';checkModelEditor();});
$('model-name').addEventListener('change',()=>{drawModelOptions();$('model-pool').value=$('model-name').value.startsWith('claude')?'claude':'codex';checkModelEditor();});
$('model-cancel').addEventListener('click',()=>{modelEdit=null;$('model-editor').hidden=true;});
$('model-reload').addEventListener('click',async()=>{const id=modelEdit?.id;if(id&&await refresh())openModelEditor(id);});
$('model-refresh').addEventListener('click',async()=>{
 $('model-refresh').disabled=true;
 try{
  modelCatalog=await modelPost('/api/models/refresh',{});showCatalogStatus();
  $('model-status').textContent=modelCatalog.notification?.sent?'Refresh requested from the watcher.':'Refresh saved. '+(modelCatalog.notification?.reason||'Waiting for the watcher.');
 }catch(error){$('model-status').textContent=error.message;}
 finally{$('model-refresh').disabled=false;}
});
$('model-form').addEventListener('submit',async e=>{
 e.preventDefault();if(modelSaving||!modelEdit)return;
 const model=selectedModel();
 const saved=state.jobs.find(j=>j.id===modelEdit.id)?.request.selection;
 if(!model&&($('model-provider').value!==saved?.instanceId||$('model-name').value!==saved?.model)){
  $('model-status').textContent='The chosen model is unavailable. Reload saved settings or choose an available model.';return;
 }
 const options={};for(const input of $('model-options').querySelectorAll('[data-model-option]'))options[input.dataset.modelOption]=input.type==='checkbox'?input.checked:input.value;
 const payload={expectedRevision:modelEdit.revision,quotaPool:$('model-pool').value,subagents:$('worker-subagents').value,subagentCap:$('worker-subagents').value==='capped'?Number($('worker-subagent-cap').value):0};
 if(model)payload.selection={instanceId:$('model-provider').value,model:model.id,options};
 modelSaving=true;$('model-fields').disabled=true;
 try{
  const result=await modelPost('/api/jobs/'+modelEdit.id+'/settings',payload);
  modelEdit=null;$('model-editor').hidden=true;await refresh();
  $('action-status').textContent=result.job.modelRevision===payload.expectedRevision?'Worker settings unchanged.':'Worker settings saved. Fresh admission checks are required.'+(state.plan.launchMode==='manual'?' Click Launch to approve these settings.':'');
 }catch(error){$('model-status').textContent=error.message+' Your draft has been retained.';}
 finally{modelSaving=false;$('model-fields').disabled=false;}
});

function syncSubagentControls(){
 const policy=$('worker-subagents').value;
 const capped=policy==='capped';
 $('worker-subagent-limit').hidden=!capped;
 $('worker-subagent-cap').disabled=!capped;
 $('worker-subagent-note').textContent=policy==='none'?'The worker performs all work itself.':capped?'The limit includes nested subagents across the entire worker’s delegation tree.':'Only one fresh agent at a time, solely for integration checks.';
}
$('worker-subagents').addEventListener('change',syncSubagentControls);

$('jobs').addEventListener('click',e=>{
 const button=e.target.closest('[data-worker-action]');if(!button)return;
 const job=state.jobs.find(j=>j.id===button.dataset.job);if(!job?.thread_id)return;
 operatorAction('/api/jobs/'+job.id+'/'+button.dataset.workerAction,{expectedThreadId:job.thread_id});
});
