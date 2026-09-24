"""Read-only, run-scoped replay metrics. Never used by the execution policy."""
import json
from collections import Counter
from zoneinfo import ZoneInfo
from datetime import datetime
from src.research.run_scope import current_run_id, active_table
from src.research.run_archive81 import list_run_archives81
from src.storage.database import get_engine_state


def exists(connection, table):
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone())


def run_dashboard81(connection):
    run_id = current_run_id(connection)
    trades = connection.execute(f"SELECT setup_id,status,result,result_dollars,opened_at FROM {active_table(connection,'paper_trades')}").fetchall()
    setups = connection.execute(f"SELECT setup_id,timeframe,created_at,status,payload_json FROM {active_table(connection,'strategy_setups')}").fetchall()
    ids = {r[0] for r in setups}
    statuses = Counter(r[3] for r in setups)
    preview = sum(r[3]=='PRE_ARMED' for r in setups)
    candidates = len(setups)-preview
    fills = sum(bool(r[4]) for r in trades)
    wins = sum(r[2]=='WIN' for r in trades)
    losses = sum(r[2]=='LOSS' for r in trades)
    stages = Counter()
    if exists(connection,'decision_traces_80'):
        for key,raw in connection.execute('SELECT setup_id,trace_json FROM decision_traces_80'):
            if key not in ids: continue
            trace=json.loads(raw)
            for stage in trace.get('stages',[]):
                stages[(stage.get('stage'),stage.get('outcome'))]+=1
    evaluations = 0
    if exists(connection,'training_evaluations_81'):
        evaluations=connection.execute('SELECT COUNT(*) FROM training_evaluations_81 WHERE run_id=?',(run_id,)).fetchone()[0]
    duplicates=0
    if exists(connection,'training_evaluations_81'):
        for raw, in connection.execute('SELECT diagnostic_json FROM training_evaluations_81 WHERE run_id=?',(run_id,)):
            duplicates += int(json.loads(raw).get('collection',{}).get('duplicate_suppressed',0))
    developing={}
    if exists(connection,'developing_setups_81'):
        developing=dict(connection.execute('SELECT state,COUNT(*) FROM developing_setups_81 WHERE run_id=? GROUP BY state',(run_id,)))
    labels={}
    if exists(connection,'research_outcomes_81'):
        labels=dict(connection.execute('SELECT outcome,COUNT(*) FROM research_outcomes_81 WHERE run_id=? GROUP BY outcome',(run_id,)))
    missed=0
    if exists(connection,'market_lessons') and 'run_id' in {r[1] for r in connection.execute('PRAGMA table_info(market_lessons)')}:
        missed=connection.execute("SELECT COUNT(*) FROM market_lessons WHERE run_id=? AND setup_found=0",(run_id,)).fetchone()[0]
    by_day=Counter(); by_session=Counter(); by_tf=Counter()
    for _,tf,created,status,_ in setups:
        if status=='PRE_ARMED':continue
        by_tf[tf]+=1
        dt=datetime.fromisoformat(created.replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York'))
        by_day[dt.date().isoformat()]+=1
        hour=dt.hour
        session='ASIA' if hour>=18 or hour<2 else 'LONDON' if hour<8 else 'NEW_YORK' if hour<16 else 'OUTSIDE'
        by_session[f'{dt.date()} {session}']+=1
    return {
        'run_id':run_id, 'label':get_engine_state(connection,'operation81_run_label','Current Run'),
        'net_pnl':round(sum(float(r[3] or 0) for r in trades if r[1]=='CLOSED'),2),
        'trades':len(trades),'wins':wins,'losses':losses,'closed':sum(r[1]=='CLOSED' for r in trades),
        'pending_open':sum(r[1] in ('PENDING','OPEN') for r in trades),'setups':len(setups),
        'funnel':{'market_evaluations':evaluations,'pre_candidates':preview,'candidates':candidates,
                  'quality_passed':stages[('QUALITY','PASSED')],'arbiter_selected':stages[('ARBITER','SELECTED')],
                  'runner_up_promotions':stages[('ARBITER','ARBITER_PROMOTED_RUNNER_UP')],
                  'registered_orders':sum(r[1] in ('PENDING','OPEN','CLOSED') for r in trades),
                  'fills':fills,'wins':wins,'losses':losses},
        'research':{'missed_moves':missed,'outcomes':labels,'developing':developing,'research_only':True},
        'duplicate_suppressed':duplicates,
        'candidate_to_fill_rate':fills/candidates if candidates else 0,
        'candidate_to_win_rate':wins/candidates if candidates else 0,
        'quality_block_rate':statuses['QUALITY_BLOCKED']/candidates if candidates else 0,
        'invalidation_rate':sum('INVALIDATED' in r[1] or r[2]=='INVALIDATED_BEFORE_ENTRY' for r in trades)/len(trades) if trades else 0,
        'stale_rate':sum('STALE' in str(r[2]) for r in trades)/len(trades) if trades else 0,
        'candidates_by_day':dict(by_day),'candidates_by_session':dict(by_session),'candidates_by_timeframe':dict(by_tf),
        'archives':list_run_archives81(connection),
        'baseline_comparisons':{archive['run_id']:compare_baseline_windows81(connection,archive['run_id'],run_id)
                                for archive in list_run_archives81(connection) if archive['run_id'] != run_id},
    }


def compare_baseline_windows81(connection, baseline_id, active_id):
    """Research-only same-timeframe/direction event-window correspondence."""
    if not exists(connection, 'market_lessons') or 'run_id' not in {r[1] for r in connection.execute('PRAGMA table_info(market_lessons)')}:
        return {}
    counts=Counter()
    for tf,direction,start,end in connection.execute("SELECT timeframe,direction,started_at,ended_at FROM market_lessons WHERE run_id=? AND setup_found=0",(baseline_id,)):
        rows=connection.execute("""SELECT s.status,p.opened_at FROM strategy_setups s
            LEFT JOIN paper_trades p ON p.setup_id=s.setup_id AND p.run_id=s.run_id
            WHERE s.run_id=? AND s.timeframe=? AND s.direction=?
              AND julianday(s.created_at) BETWEEN julianday(?) AND julianday(?)""",(active_id,tf,direction,start,end)).fetchall()
        label='no_prospective_candidate'
        if any(row[1] for row in rows):label='filled_trade'
        elif any(row[0] in ('PENDING','REGISTERED','OPEN','CLOSED') for row in rows):label='valid_candidate'
        elif any(row[0]!='PRE_ARMED' for row in rows):label='rejected_candidate'
        elif rows:label='pre_candidate_only'
        counts[label]+=1
    return {'method':'Same timeframe/direction, candidate timestamp within baseline move window; research-only', 'counts':dict(counts)}
