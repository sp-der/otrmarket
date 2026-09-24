(() => {
  const money = value => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(value || 0);
  async function refresh() {
    try {
      const response = await fetch('/market/api/otr81/current-run', {credentials:'same-origin'});
      if (!response.ok) return;
      const data = await response.json();
      document.getElementById('activeRun81').textContent = `${data.label} · ${data.run_id} · ${money(data.net_pnl)} · ${data.wins}W / ${data.losses}L · ${data.closed} closed · ${data.pending_open} pending/open`;
      document.getElementById('runFunnel81').textContent = Object.entries(data.funnel).map(([k,v]) => `${k.replaceAll('_',' ')}: ${v}`).join(' · ');
      document.getElementById('runResearch81').textContent = JSON.stringify({duplicate_suppressed:data.duplicate_suppressed,fill_rate:data.candidate_to_fill_rate,win_rate:data.candidate_to_win_rate,quality_block_rate:data.quality_block_rate,invalidation_rate:data.invalidation_rate,stale_rate:data.stale_rate,by_day:data.candidates_by_day,by_session:data.candidates_by_session,by_timeframe:data.candidates_by_timeframe,research:data.research,baseline_comparisons:data.baseline_comparisons},null,2);
      const history = document.getElementById('runHistory81');
      history.replaceChildren();
      for (const run of data.archives) {
        const block = document.createElement('p');
        const details = run.metadata || {};
        block.textContent = `${run.label} · ${run.run_id} · ${money(run.net_pnl)} · ${run.wins}W / ${run.losses}L · ${run.trade_count} trades · ${details.replay_start || 'Date unavailable'} — ${details.replay_end || 'Date unavailable'} `;
        for (const kind of ['trades','setups']) {
          const link = document.createElement('a');
          link.href = `/market/api/otr81/run-archives/${encodeURIComponent(run.archive_id)}/${kind}`;
          link.textContent = ` View ${kind} `;
          link.target = '_blank'; link.rel = 'noopener'; block.append(link);
        }
        history.append(block);
      }
      if (!data.archives.length) history.textContent = 'No archived runs yet.';
    } catch (error) { console.warn('Run ledger unavailable', error); }
  }
  refresh(); setInterval(refresh,15000);
})();
