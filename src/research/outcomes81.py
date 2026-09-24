"""Research-only labels from subsequently observed bars, separate from decisions."""
import json
from datetime import timedelta

from src.otr8.runner_up81 import utc
from src.research.run_scope import current_run_id


def ensure_outcomes81(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS research_outcomes_81 (
        run_id TEXT NOT NULL, setup_id TEXT NOT NULL, outcome TEXT NOT NULL,
        evaluated_at TEXT NOT NULL, details_json TEXT NOT NULL,
        PRIMARY KEY(run_id,setup_id))''')


def label_window81(snapshot, bars, now):
    """Conservative OHLC labeling: ambiguous entry/stop bars are NOT_EVALUABLE."""
    created = utc(snapshot['created_at'])
    now = utc(now)
    deadline = created + timedelta(hours=4)
    entry, stop, target = (float(snapshot[k]) for k in ('entry_price','stop_price','target_price'))
    risk = abs(entry-stop)
    bullish = snapshot['direction'] == 'bullish'
    if risk <= 0 or not (stop < entry < target if bullish else target < entry < stop):
        return 'NOT_EVALUABLE', {'reason': 'No valid frozen entry geometry'}
    entered = False
    reached = 0
    seen = False
    previous_close = created
    for bar in sorted(bars, key=lambda b: utc(b.close_time)):
        if utc(bar.open_time) < created or utc(bar.close_time) <= created or utc(bar.close_time) > min(now,deadline):
            continue
        if utc(bar.open_time) > previous_close + timedelta(minutes=1):
            return 'NOT_EVALUABLE', {'reason': 'Missing subsequent market bars'}
        previous_close = utc(bar.close_time)
        seen = True
        stop_hit = bar.low <= stop if bullish else bar.high >= stop
        if not entered:
            touched = bar.low <= entry <= bar.high
            if stop_hit and touched:
                return 'NOT_EVALUABLE', {'reason': 'Entry/stop sequence ambiguous within OHLC bar'}
            if stop_hit:
                return 'INVALIDATED_FIRST', {}
            if not touched:
                continue
            entered = True
            # Entry-bar favorable range may precede entry; never credit it.
            continue
        favorable = (bar.high-entry)/risk if bullish else (entry-bar.low)/risk
        if stop_hit:
            if favorable >= max(1,reached+1):
                return 'NOT_EVALUABLE', {'reason': 'Stop/target sequence ambiguous within OHLC bar'}
            return (f'WOULD_HAVE_REACHED_{reached}R' if reached else 'INVALIDATED_FIRST'), {}
        reached = max(reached, min(3,int(max(0,favorable))))
        if reached == 3:
            return 'WOULD_HAVE_REACHED_3R', {}
    if now >= deadline:
        return (f'WOULD_HAVE_REACHED_{reached}R' if reached else
                'ENTRY_NEVER_TOUCHED' if seen and not entered else 'NOT_EVALUABLE'), {}
    return None, {}


def update_outcomes81(connection, symbol, timeframe, histories, event_time):
    ensure_outcomes81(connection)
    run_id = current_run_id(connection)
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='training_decisions_72t'").fetchone()
    if not exists:
        return
    rows = connection.execute('''SELECT d.setup_id,d.created_at,d.direction,d.entry_price,d.stop_price,d.target_price
        FROM training_decisions_72t d LEFT JOIN research_outcomes_81 o
          ON o.run_id=d.run_id AND o.setup_id=d.setup_id
        WHERE d.run_id=? AND d.symbol=? AND d.timeframe=? AND o.setup_id IS NULL
          AND d.status NOT IN ('OPEN','CLOSED')''', (run_id,symbol,timeframe)).fetchall()
    bars = histories.get((symbol,timeframe), [])
    for row in rows:
        snapshot = dict(zip(('setup_id','created_at','direction','entry_price','stop_price','target_price'),row))
        label, details = label_window81(snapshot,bars,event_time)
        if label:
            connection.execute('INSERT INTO research_outcomes_81 VALUES (?,?,?,?,?) ON CONFLICT(run_id,setup_id) DO NOTHING',
                               (run_id,row[0],label,event_time.isoformat(),json.dumps(details)))
    connection.commit()
