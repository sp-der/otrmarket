const $=s=>document.querySelector(s);
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=1)=>Number.isFinite(Number(v))?Number(v).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const signed=(v,d=2)=>Number.isFinite(Number(v))?`${Number(v)>=0?'+':''}${num(v,d)}`:'—';
const pct=v=>Number.isFinite(Number(v))?`${num(v,1)}%`:'—';
const money=v=>Number.isFinite(Number(v))?`${Number(v)<0?'-':''}$${Math.abs(Number(v)).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`:'—';
const clsR=v=>Number(v)>0?'positive':Number(v)<0?'negative':'';

async function get(url){
  const r=await fetch(url,{cache:'no-store'});
  if(r.status===401){location.href='/market/';throw Error('Authentication required')}
  if(!r.ok)throw Error(`${r.status}`);
  return r.json();
}

function metric(label,value,note=''){
  return `<article class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note?`<small>${esc(note)}</small>`:''}</article>`;
}

function renderCorpus(d){
  const c=d.corpus||{};
  $('#corpusCards').innerHTML=[
    ['Decisions',c.decisions??0,`${c.current_run_decisions??0} this run`],
    ['Actual outcomes',c.closed_actual_trades??0,`${c.wins??0}W / ${c.losses??0}L`],
    ['Rejected outcomes',c.resolved_counterfactuals??0,`${c.counterfactual_would_win??0} would win`],
    ['Missed moves',c.missed_opportunities??0,`${c.market_lessons??0} large-move lessons`],
    ['Shadow outcomes',c.shadow_closed??0,'research-only variants'],
    ['Backtest runs',c.backtest_runs??0,'immutable research runs'],
  ].map(x=>metric(...x)).join('');
}

function renderReadiness(d){
  const r=d.readiness||{};
  $('#readinessValue').textContent=pct(r.overall??0);
  $('#labStage').textContent=String(r.stage||'COLLECTING').replaceAll('_',' ');
  const items=[
    ['Decision recorder',r.decision_recorder],
    ['Outcome labels',r.outcome_labels],
    ['Missed-move library',r.missed_move_library],
    ['Shadow ranker',r.shadow_ranker],
  ];
  $('#readinessBars').innerHTML=items.map(([label,value])=>`<div class="readiness-item"><div class="readiness-line"><span>${esc(label)}</span><strong>${pct(value??0)}</strong></div><div class="progress"><i style="width:${Math.max(0,Math.min(100,Number(value)||0))}%"></i></div></div>`).join('');
}

function renderRanker(d){
  const rows=d.shadow_ranker||[];
  const el=$('#rankerList');
  el.classList.toggle('empty',!rows.length);
  el.innerHTML=rows.length?rows.map(r=>`<div class="rank-row"><div class="rank-top"><div><div class="rank-title">${esc(r.timeframe)} · ${esc(r.strategy)}</div><div class="rank-meta"><span>${r.sample} samples</span><span>${pct(r.win_rate)} win</span><span class="${clsR(r.avg_r)}">${signed(r.avg_r)}R avg</span><span>${pct(r.confidence)} confidence</span></div></div><div><span class="status ${String(r.status||'').toLowerCase()}">${esc(r.status)}</span><div class="rank-score">${num(r.evidence_score,1)}</div></div></div></div>`).join(''):'Collecting closed outcomes.';
}

function renderTargets(d){
  const x=d.extended_targets||{};
  const sample=Number(x.sample||0);
  const rate=(n)=>sample?`${Math.round(Number(n||0)/sample*100)}%`:'—';
  $('#targetAudit').innerHTML=[['1R',x.reached_1r],['2R',x.reached_2r],['3R',x.reached_3r],['4R',x.reached_4r]].map(([label,value])=>`<div class="target-cell"><span>Reached ${label}</span><strong>${rate(value)}</strong><small class="muted">${value||0}/${sample}</small></div>`).join('');
}

function renderMissed(d){
  const rows=d.recent_missed||[];
  $('#missedCount').textContent=`${d.corpus?.missed_opportunities||0} MISSED`;
  const el=$('#missedList');el.classList.toggle('empty',!rows.length);
  el.innerHTML=rows.length?rows.map(r=>`<article class="event-card"><strong>${esc(r.timeframe)} · ${esc(String(r.direction||'').toUpperCase())} · ${signed(r.move_points,1)} pts</strong><div class="event-meta"><span>${esc(r.started_at)}</span><span>${esc(r.setup_status||'NO CANDIDATE')}</span></div><p>${esc(r.summary||r.block_reason||'Large move was observed without an executable setup candidate.')}</p></article>`).join(''):'No missed large-move lessons yet.';
}

function renderCounterfactuals(d){
  const rows=d.recent_counterfactuals||[];
  const el=$('#counterfactualList');el.classList.toggle('empty',!rows.length);
  el.innerHTML=rows.length?rows.map(r=>`<div class="counter-row"><div class="counter-top"><strong>${esc(r.timeframe)} · ${esc(String(r.direction||'').toUpperCase())}</strong><span class="status ${String(r.outcome||'').includes('WIN')?'promising':String(r.outcome||'').includes('LOSE')?'weak':''}">${esc(r.outcome)}</span></div><div class="counter-meta"><span>MFE ${signed(r.max_favorable_r)}R</span><span>MAE ${signed(r.max_adverse_r)}R</span><span>${esc(r.blocked_status)}</span></div><p>${esc(r.blocked_reason||'Blocked by the live quality pipeline.')}</p></div>`).join(''):'No resolved rejected setups yet.';
}

function renderFeatures(d){
  const rows=d.top_learning_features||[];
  const el=$('#featureList');el.classList.toggle('empty',!rows.length);
  el.innerHTML=rows.length?rows.map(r=>`<div class="feature-row"><div><strong>${esc(String(r.feature||'').replaceAll('_',' '))}</strong><small>${r.bullish_hits||0} bullish · ${r.bearish_hits||0} bearish</small></div><div><strong>${r.lesson_hits||0}</strong><small>${num(r.total_move_points,1)} pts</small></div></div>`).join(''):'No feature evidence yet.';
}

function renderWalk(d){
  const w=d.walk_forward||{};
  $('#walkStatus').textContent=String(w.status||'COLLECTING').replaceAll('_',' ');
  if(w.status==='COLLECTING'){
    $('#walkForward').innerHTML=`<div class="walk-big">${w.sample||0}/${w.minimum_sample||20}</div><p class="fineprint">${esc(w.note||'Collecting chronological outcomes.')}</p>`;return;
  }
  $('#walkForward').innerHTML=`<div class="walk-big">70 / 30</div><div class="walk-stats"><div class="walk-stat"><span>TRAIN</span><strong>${w.train_sample||0}</strong></div><div class="walk-stat"><span>UNSEEN TEST</span><strong>${w.test_sample||0}</strong></div><div class="walk-stat"><span>SELECTED</span><strong>${w.selected_test_sample||0}</strong></div><div class="walk-stat"><span>SELECTED R</span><strong class="${clsR(w.selected_test_total_r)}">${signed(w.selected_test_total_r)}R</strong></div><div class="walk-stat"><span>SELECTED AVG</span><strong class="${clsR(w.selected_test_avg_r)}">${signed(w.selected_test_avg_r)}R</strong></div><div class="walk-stat"><span>ALL TEST AVG</span><strong class="${clsR(w.all_test_avg_r)}">${signed(w.all_test_avg_r)}R</strong></div></div><p class="fineprint">${esc(w.note||'Research-only chronological holdout.')}</p>`;
}

function renderMacro(d){
  const m=d.macro_4h||{};const direction=String(m.direction||'unknown').toLowerCase();
  $('#macro4h').innerHTML=`<span class="eyebrow">4H MACRO</span><div class="macro-direction ${esc(direction)}">${esc(direction)}</div><p>${esc(m.note||'4H context is warming up.')}</p><div class="counter-meta"><span>${m.bars||0} bars</span><span>${m.last_close?money(m.last_close):'—'}</span><span>${esc(m.last_close_time||'')}</span></div>`;
}

function renderDecisions(d){
  const rows=d.recent_decisions||[];const el=$('#decisionList');el.classList.toggle('empty',!rows.length);
  el.innerHTML=rows.length?rows.map(r=>`<div class="decision-row"><div><strong>${esc(r.timeframe)} · ${esc(String(r.direction||'').toUpperCase())}</strong><div class="decision-meta"><span>${esc(r.created_at)}</span></div></div><div><strong>${esc(r.strategy)}</strong><div class="decision-meta"><span>${esc(r.grade||'—')}</span><span>${num(r.risk_reward,2)}R offered</span></div></div><p>${esc(String(r.status||'').replaceAll('_',' '))}</p><span class="status">${esc(r.trigger_type||'—')}</span></div>`).join(''):'Waiting for decisions.';
}

function renderRuns(runs){
  $('#runCount').textContent=`${runs.length} RUN${runs.length===1?'':'S'}`;const el=$('#runList');el.classList.toggle('empty',!runs.length);
  el.innerHTML=runs.length?runs.slice(0,12).map(r=>`<div class="run-row"><div><strong>${esc(r.run_id)}</strong><div class="run-meta"><span>${esc(r.engine_version)}</span><span>${esc(r.replay_mode)}</span></div></div><div><strong>${r.metrics?.total_trades??0} trades</strong><div class="run-meta"><span>${pct(r.metrics?.win_rate)}</span><span>PF ${num(r.metrics?.profit_factor,2)}</span></div></div><div><strong class="${clsR(r.metrics?.net_pnl)}">${money(r.metrics?.net_pnl)}</strong><div class="run-meta"><span>DD ${money(r.metrics?.maximum_drawdown_dollars)}</span></div></div></div>`).join(''):'No research runs found.';
}

function renderTraining(d){
  $('#labRun').textContent=`RUN ${d.run_id||'—'} · ${d.build||'7.2T'}`;
  $('#labUpdated').textContent=d.generated_at?`Updated ${new Date(d.generated_at).toLocaleTimeString()}`:'Waiting for data';
  renderCorpus(d);renderReadiness(d);renderRanker(d);renderTargets(d);renderMissed(d);renderCounterfactuals(d);renderFeatures(d);renderWalk(d);renderMacro(d);renderDecisions(d);
}

async function refreshTraining(){
  try{renderTraining(await get(`/market/api/training?lab=${Date.now()}`))}
  catch(e){$('#labStage').textContent='OFFLINE';$('#labUpdated').textContent=`Training API unavailable: ${e.message}`}
  setTimeout(refreshTraining,5000);
}

(async()=>{
  refreshTraining();
  try{const r=await get('/market/api/research/runs');renderRuns(r.runs||[])}
  catch(e){$('#runList').textContent=`Backtest store unavailable: ${e.message}`}
})();
