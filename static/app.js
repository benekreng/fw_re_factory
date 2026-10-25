'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label=v=>String(v??'').replaceAll('_',' ');
const fmt=new Intl.NumberFormat(undefined,{maximumFractionDigits:1});
const time=v=>v?new Date(v).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Unknown';
const relative=v=>{if(!v)return'Unknown';const n=(new Date(v)-Date.now())/60000;return n<0?'Due / awaiting sample':n<60?`${Math.ceil(n)} min`:`${fmt.format(n/60)} h`;};
const empty=(a,b='')=>`<div class="empty"><strong>${esc(a)}</strong>${esc(b)}</div>`;
const pill=(value,style='')=>`<span class="pill ${style}">${esc(label(value))}</span>`;
const kv=(a,b)=>`<div class="kv"><span>${esc(a)}</span><span>${b}</span></div>`;
const thread=(id,text='Open thread')=>/^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$/.test(id||'')?`<a href="t3-thread://v1/${esc(id)}">${esc(text)} ↗</a>`:'—';
const active=new Set(['preparing','prepared','launching','running','usage_limited','waiting','review_pending','incomplete','attention','recovery_pending','replacement_ready','replacement_launching']);
let state=null;
function renderJobs(){
 if(!state)return;const filter=$('filter').value;
 const jobs=state.jobs.filter(j=>filter==='all'||(filter==='active'?active.has(j.state):filter==='attention'?['attention','incomplete','review_pending'].includes(j.state):j.state===filter));
 $('jobs').innerHTML=jobs.length?`<div class="table-wrap"><table><thead><tr><th>DEVICE / THREAD</th><th>STATE</th><th>WORKER PROFILE</th><th>PROGRESS / LAST SEEN</th><th>BROWSER</th></tr></thead><tbody>${jobs.map(j=>`<tr><td><strong>${esc(j.name)}</strong><small>${thread(j.thread_id,j.slug)}</small></td><td>${pill(j.state,['attention','incomplete','usage_limited'].includes(j.state)?'warn':j.state==='queued'?'neutral':'')}</td><td>${esc(j.request.selection.model)}<small>${esc(j.request.selection.instanceId)} · ${esc(JSON.stringify(j.request.selection.options))}</small><small>Subagents: ${esc(j.request.subagents)}${j.request.subagents==='capped'?' / '+esc(j.request.subagentCap):''}</small></td><td>${esc(j.observation?.stage||j.observation?.summary||j.error||'Awaiting observation')}<small>${esc(time(j.observation?.observedAt||j.updated))}</small></td><td>${j.observation?.browserVerified?`<a href="http://127.0.0.1:${Number(j.port)}" target="_blank" rel="noreferrer">Open :${Number(j.port)} ↗</a>`:'Unverified'}</td></tr>`).join('')}</tbody></table></div>`:empty(state.jobs.length?'No workers match this filter':'Your next project starts here',state.jobs.length?'Choose another state.':'Run re-launch in a terminal to add approved firmware jobs.');
}
function renderAccounts(){
 const usage=state.usage;const stale=!state.plan.quotaFresh;
 $('sample-time').textContent=usage?`Sample ${time(usage.time)}${stale?' · STALE / INCOMPLETE':''}`:'No samples yet';
 $('accounts').innerHTML=usage?.accounts?.length?usage.accounts.map(a=>{
 const f=state.forecasts.filter(f=>f.account===a.account);const count=a.resets?.available_count;
 const grants=a.resets?.grants||a.resets?.credits||[];
 return `<article class="panel account"><div class="panel-heading"><div><h3>${esc(a.provider==='claude'?'Claude':'Codex')}</h3><span class="muted small">${esc(a.account)}</span></div>${pill(a.disabled?'disabled':a.errors.length?'partial data':stale?'stale':'sampled',a.errors.length||stale?'warn':'neutral')}</div>${a.windows.length?a.windows.map(w=>{const p=f.find(x=>x.window===w.window);return `<div class="window"><div class="window-label"><span>${esc(label(w.window))}${w.scope!=='all'?' · scoped':''}</span><strong>${fmt.format(w.used)}%</strong></div><div class="bar"><div class="fill ${w.used>85?'high':''}" style="width:${w.used}%"></div></div><div class="window-meta"><span>Reset ${esc(relative(w.reset_at))}</span><span>${p?.ratePerHour!=null?fmt.format(p.ratePerHour)+' pp/h':'Measuring rate'}</span></div><div class="window-meta"><span>${esc(time(w.reset_at))}</span><span>${p?.exhaustHours!=null?'Limit in ~'+fmt.format(p.exhaustHours)+' h':''}</span></div>${p?.resetEvent?`<p class="small warn-text">${esc(p.resetEvent.kind)} · ${fmt.format(p.resetEvent.drop)} pp decrease</p>`:''}</div>`;}).join(''):empty('Usage unavailable','Unknown is not zero.')}${a.errors.length?`<p class="small warn-text">${esc(a.errors.join('; '))}</p>`:''}<div class="note">Reset vouchers: ${count==null?'unknown':esc(count)}${grants.map(g=>`<p>${esc(g.label||g.status||'Grant')} · expires ${esc(time(g.ends_at||g.expires_at||g.expiresAt))}${g.usable_now?' · usable now':''}</p>`).join('')}<p>Rotation and redemption require a verified control interface.</p></div></article>`;
 }).join(''):empty('Waiting for account data',usage?.error||'The controller will sample configured accounts.');
}
function renderChart(){
 const pick=$('window-filter').value;const rows=state.history.filter(r=>pick==='all'||(pick==='short'?r.seconds!=null&&r.seconds<=86400:r.seconds!=null&&r.seconds>86400));
 if(!rows.length){$('chart').innerHTML=empty('No history for this window','Usage measurements will appear here as samples accumulate.');$('legend').innerHTML='';return;}
 const groups=new Map();for(const r of rows){const key=r.account+' / '+label(r.window);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
 const colors=['#9cdebd','#8db8eb','#e9be78','#c1a5eb','#e99595','#65c6c9','#b4c17b','#d5a2c5'];
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
 state=data;$('error').hidden=true;$('sync').textContent='Updated '+new Date(data.time).toLocaleTimeString();$('connection').className='dot';
 const stats=[['ACTIVE WORKERS',data.jobs.filter(j=>active.has(j.state)).length,'of '+data.plan.maxParallel+' concurrent slots'],['APPROVED QUEUE',data.plan.queued,'waiting for capacity'],['VERIFIED COMPLETE',data.jobs.filter(j=>j.state==='verified_complete').length,'independently reviewed'],['NEEDS ATTENTION',data.jobs.filter(j=>['attention','incomplete','usage_limited'].includes(j.state)).length,'limits, findings or blockers']];
 $('stats').innerHTML=stats.map(([l,v,n])=>`<div class="stat"><span class="label">${l}</span><span class="value">${v}</span><small>${esc(n)}</small></div>`).join('');
 $('watch-state').textContent=data.config.watcherMessagesPaused?'messages paused':label(data.watcher.phase);
 $('watcher').innerHTML=kv('Thread',thread(data.watcher.thread))+kv('Ownership generation',esc(data.watcher.epoch))+kv('Last heartbeat',esc(time(data.watcher.heartbeat)))+kv('Wake interval',esc(data.config.wakeSeconds??'—')+' seconds')+`<p class="note">${data.watcher.thread?'Changing the watcher preserves existing workers and history.':'A watcher is created when you submit your first queue request.'}</p>`;
 $('policy').textContent=label(data.plan.policy);
 const next=data.plan.decisions.find(d=>d.admit)||data.plan.decisions[0];
 $('admission').innerHTML=next?`<p>${esc(next.name)} ${pill(next.admit?'eligible to start':'hold',next.admit?'':'warn')}</p><p class="muted">${esc(next.reason)}</p>`:empty('No pending launch decisions','Add firmware groups with re-launch.');
 $('admission').innerHTML+=`<p class="note">Usage ${data.plan.quotaFresh?'fresh':'missing, incomplete or stale'} · one incremental addition per cycle. Forecasts include other activity on each account.</p>`;
 renderJobs();renderAccounts();renderChart();renderTokens();
 $('recovery-state').textContent=data.recoveryPolicy?.enabled?'policy enabled':'disabled';
 $('recoveries').innerHTML=data.recoveries?.length?data.recoveries.slice(0,12).map(r=>`<div class="review"><strong>${esc(data.jobs.find(j=>j.id===r.job)?.name||r.job)}</strong> ${pill(r.phase,['attention','launching'].includes(r.phase)?'warn':'neutral')}<p>Rule: ${esc(r.rule_id)} · repair attempts: ${Number(r.repair_attempts)} · ${esc(time(r.updated))}</p>${r.error?`<p class="warn-text">${esc(r.error)}</p>`:''}</div>`).join(''):empty('No thread recovery incidents',data.recoveryPolicy?.enabled?'The watcher uses your saved criteria and bounded repair procedure.':'Waiting for your broken-thread criteria and repair instructions.');
 const reviews=data.jobs.filter(j=>j.review||j.state==='review_pending');$('reviews').innerHTML=reviews.length?reviews.map(j=>`<div class="review"><strong>${esc(j.name)}</strong> ${pill(j.review?.verdict||'review pending')}<p>${esc(j.review?.summary||'Independent audit pending.')}</p>${(j.review?.findings||[]).map(f=>`<p class="warn-text">${esc(typeof f==='object'?JSON.stringify(f):f)}</p>`).join('')}<p>${j.review?.commit?'Commit '+esc(j.review.commit.slice(0,12)):''} ${thread(j.thread_id)}</p></div>`).join(''):empty('No completion claims yet','Completed workers need source, evidence, checks and browser verification before acceptance.');
 $('events').innerHTML=data.events.length?data.events.slice(0,35).map(e=>`<div class="event"><time>${esc(time(e.time))}</time><span>${esc(label(e.kind))}</span><pre>${esc(e.detail)}</pre></div>`).join(''):empty('A clean audit trail','Launches, handovers and controller actions will appear here.');
}
async function refresh(){try{const r=await fetch('/api/status');if(!r.ok)throw new Error('Dashboard request failed: HTTP '+r.status);render(await r.json());}catch(e){$('error').hidden=false;$('error').textContent=e.message+' Displayed data may be stale.';$('sync').textContent='Connection lost';}}
$('filter').addEventListener('change',renderJobs);$('window-filter').addEventListener('change',()=>state&&renderChart());refresh();setInterval(refresh,15000);
