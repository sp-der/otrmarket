"""Auditable observation lifecycle. This module never registers orders."""
from datetime import timedelta
import hashlib
import json

from src.otr8.runner_up81 import utc
from src.research.run_scope import current_run_id


def observe_developing81(connection, candidates, symbol, timeframe, histories, now):
    connection.execute('''CREATE TABLE IF NOT EXISTS developing_setups_81 (
        run_id TEXT NOT NULL, thesis_id TEXT NOT NULL, setup_id TEXT NOT NULL,
        symbol TEXT NOT NULL,timeframe TEXT NOT NULL,direction TEXT NOT NULL,
        created_at TEXT NOT NULL,snapshot_json TEXT NOT NULL,state TEXT NOT NULL,
        resolved_at TEXT,promoted_setup_id TEXT,
        PRIMARY KEY(run_id,thesis_id))''')
    run_id=current_run_id(connection)
    for setup in candidates:
        metadata=setup.metadata or {}
        if metadata.get('candidate_source_80') != 'EARLY_ARM_72H': continue
        displacement=getattr(getattr(setup,'displacement',None),'candle_time',None)
        # Only link a known originating displacement; never infer a thesis after the move.
        if displacement is None: continue
        thesis=hashlib.sha256(f'{symbol}|{timeframe}|{setup.direction}|{displacement}'.encode()).hexdigest()[:24]
        if metadata.get('preview_only_80'):
            snapshot={'entry':setup.entry_price,'stop':setup.stop_price,'target':setup.target_price,
                      'metadata':metadata,'displacement_time':str(displacement)}
            connection.execute('''INSERT INTO developing_setups_81
                (run_id,thesis_id,setup_id,symbol,timeframe,direction,created_at,snapshot_json,state)
                VALUES (?,?,?,?,?,?,?,?, 'PRE_ARMED') ON CONFLICT(run_id,thesis_id) DO NOTHING''',
                (run_id,thesis,setup.setup_id,symbol,timeframe,setup.direction,setup.created_at.isoformat(),json.dumps(snapshot,default=str,sort_keys=True)))
        else:
            state='PROMOTED_TO_CANDIDATE' if setup.status in ('PENDING','REGISTERED','OPEN') else setup.status
            connection.execute("UPDATE developing_setups_81 SET state=?,resolved_at=?,promoted_setup_id=? WHERE run_id=? AND thesis_id=? AND state='PRE_ARMED'",
                               (state,now.isoformat(),setup.setup_id,run_id,thesis))
    bars=histories.get((symbol,timeframe),[])
    for thesis,created,direction,raw in connection.execute("SELECT thesis_id,created_at,direction,snapshot_json FROM developing_setups_81 WHERE run_id=? AND symbol=? AND timeframe=? AND state='PRE_ARMED'",(run_id,symbol,timeframe)).fetchall():
        snapshot=json.loads(raw); state=None
        subsequent=[b for b in bars if utc(b.open_time)>=utc(created) and utc(b.close_time)>utc(created) and utc(b.close_time)<=now]
        if any(b.low<=snapshot['stop'] if direction=='bullish' else b.high>=snapshot['stop'] for b in subsequent):state='INVALIDATED'
        elif now>=utc(created)+timedelta(hours=1):state='EXPIRED'
        if state:
            connection.execute('UPDATE developing_setups_81 SET state=?,resolved_at=? WHERE run_id=? AND thesis_id=?',(state,now.isoformat(),run_id,thesis))
    connection.commit()
