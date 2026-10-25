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
const active=new Set(['preparing','prepared','launching','running','usage_limited','waiting','review_pending','incomplete','attention','recovery_pending','replacement_ready','replacement_launching']);
let state=null;
let submitting=false;
function launchControl(j){
 if(!['queued','preparing','prepared','replacement_ready'].includes(j.state))return '';
 return `<button type="button" data-launch="${esc(j.id)}" ${submitting?'disabled':''}>${j.launchApproval?'Retry launch':'Launch'}</button>`;
}
function workerSettings(j){
 const options=j.request.selection.options||{};
 const settings=Object.entries(options).map(([key,value])=>key==='reasoningEffort'?label(value).replace(/^./,c=>c.toUpperCase())+' reasoning':key==='serviceTier'?(value==='default'?'Standard tier':label(value)+' tier'):label(key)+': '+value);
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
  $('action-status').textContent=result.approval?(result.notification?.sent?'Launch requested; watcher notified.':'Launch saved. '+(result.notification?.reason||'Waiting for the watcher.')):'Launch mode: '+result.launchMode+'.';
 }catch(e){$('action-status').textContent=e.message;}
 finally{submitting=false;renderJobs();$('launch-mode').value=state.plan.launchMode;$('launch-mode').disabled=false;}
}

function renderJobs(){
 if(!state)return;
 const filter=$('filter').value;
 const jobs=state.jobs.filter(j=>filter==='all'||(filter==='active'?active.has(j.state):filter==='attention'?['attention','incomplete','review_pending'].includes(j.state):j.state===filter));
 const rows=jobs.map(j=>{
  const decision=state.plan.decisions.find(d=>d.jobId===j.id);
  const progress=(j.state==='queued'&&decision?.reason)||j.observation?.stage||j.observation?.summary||j.error||'Waiting for the watcher';
  const preview=progress.length>220?progress.slice(0,217)+'…':progress;
  const browser=j.observation?.browserVerified?`<a href="http://127.0.0.1:${Number(j.port)}" target="_blank" rel="noreferrer">Browser :${Number(j.port)}</a>`:'';
  return `<tr><td class="device">${j.thread_id?thread(j.thread_id,j.name):`<strong>${esc(j.name)}</strong>`}</td><td>${pill(j.state,['attention','incomplete','usage_limited'].includes(j.state)?'warn':'')}</td><td class="profile">${esc(j.request.selection.model)}<small>${esc(workerSettings(j))}</small></td><td class="progress"><span title="${esc(progress)}">${esc(preview)}</span><small>${esc(time(j.observation?.observedAt||j.updated))}</small></td><td class="actions">${launchControl(j)}${browser}</td></tr>`;
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
 renderJobs();renderAccounts();renderChart();renderTokens();
 $('recovery-state').textContent=data.recoveryPolicy?.enabled?'policy enabled':'disabled';
 $('recoveries').innerHTML=data.recoveries?.length?data.recoveries.slice(0,12).map(r=>`<div class="review"><strong>${esc(data.jobs.find(j=>j.id===r.job)?.name||r.job)}</strong> ${pill(r.phase,['attention','launching'].includes(r.phase)?'warn':'neutral')}<p>Rule: ${esc(r.rule_id)} · repair attempts: ${Number(r.repair_attempts)} · ${esc(time(r.updated))}</p>${r.error?`<p class="warn-text">${esc(r.error)}</p>`:''}</div>`).join(''):empty('No thread recovery incidents',data.recoveryPolicy?.enabled?'The watcher uses your saved criteria and bounded repair procedure.':'Waiting for your broken-thread criteria and repair instructions.');
 const reviews=data.jobs.filter(j=>j.review||j.state==='review_pending');$('reviews').innerHTML=reviews.length?reviews.map(j=>`<div class="review"><strong>${esc(j.name)}</strong> ${pill(j.review?.verdict||'review pending')}<p>${esc(j.review?.summary||'Independent audit pending.')}</p>${(j.review?.findings||[]).map(f=>`<p class="warn-text">${esc(typeof f==='object'?JSON.stringify(f):f)}</p>`).join('')}<p>${j.review?.commit?'Commit '+esc(j.review.commit.slice(0,12)):''} ${thread(j.thread_id)}</p></div>`).join(''):empty('No completion claims yet','Completed workers need source, evidence, checks and browser verification before acceptance.');
 const openedEvents=new Set([...document.querySelectorAll('#events details[open]')].map(e=>e.dataset.event));
 $('events').innerHTML=data.events.length?data.events.slice(0,35).map(e=>`<details class="event" data-event="${esc(e.id)}" ${openedEvents.has(String(e.id))?'open':''}><summary><time>${esc(time(e.time))}</time><span>${esc(label(e.kind))}</span></summary><pre>${esc(e.detail)}</pre></details>`).join(''):empty('No activity yet.');

}
async function refresh(){try{const r=await fetch('/api/status');if(!r.ok)throw new Error('Dashboard request failed: HTTP '+r.status);render(await r.json());}catch(e){$('error').hidden=false;$('error').textContent=e.message+' Displayed data may be stale.';$('sync').textContent='Connection lost';}}
$('jobs').addEventListener('click',e=>{const button=e.target.closest('[data-launch]');if(button)operatorAction('/api/jobs/'+button.dataset.launch+'/launch',{});});
$('launch-mode').addEventListener('change',e=>operatorAction('/api/launch-mode',{mode:e.target.value}));
$('filter').addEventListener('change',renderJobs);$('window-filter').addEventListener('change',()=>state&&renderChart());refresh();setInterval(refresh,15000);
