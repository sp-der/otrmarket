from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .engine import digest
from .schema import PHASE6_SCHEMA_SQL


class Phase6Store:
    def __init__(self, database: str | Path): self.database=Path(database)
    def initialize(self):
        self.database.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.database) as c: c.executescript(PHASE6_SCHEMA_SQL)
    def create_study(self, definition: dict, candidates: list[dict], folds: list[dict]):
        created=definition.get("created_at") or datetime.now(timezone.utc).isoformat()
        payload={**definition,"created_at":created}; definition_digest=digest(payload)
        with sqlite3.connect(self.database) as c:
            c.execute("INSERT INTO phase6_studies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(
              payload["study_id"],payload["hypothesis"],payload["capture_id"],payload["capture_digest"],payload["git_commit"],
              payload["start_time"],payload["end_time"],payload["replay_mode"],json.dumps(payload["limitations"],sort_keys=True),
              json.dumps(payload["preregistration"],sort_keys=True),created,definition_digest))
            for item in candidates:
                c.execute("INSERT INTO phase6_candidates VALUES(?,?,?,?,?,?,?)",(item["candidate_id"],payload["study_id"],item["name"],item["hypothesis"],json.dumps(item["configuration"],sort_keys=True),json.dumps(item["configuration_diff"],sort_keys=True),item["definition_digest"]))
            for item in folds:
                c.execute("INSERT INTO phase6_folds VALUES(?,?,?,?,?,?,?)",(payload["study_id"],item["fold_id"],item["is_start"],item["is_end"],item["oos_start"],item["oos_end"],0))
        return definition_digest
    def append_run(self, row: dict):
        with sqlite3.connect(self.database) as c:
            c.execute("INSERT INTO phase6_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(row["run_id"],row["study_id"],row["candidate_id"],row["fold_id"],row["sample_role"],json.dumps(row["manifest"],sort_keys=True),json.dumps(row["metrics"],sort_keys=True),json.dumps(row["segments"],sort_keys=True),json.dumps(row["behavior"],sort_keys=True),row["run_digest"],row["decision_digest"],row["trade_digest"],row["status"]))
    def append_result(self, study_id: str, candidate_id: str, result_type: str, payload: dict):
        value=digest({"study_id":study_id,"candidate_id":candidate_id,"result_type":result_type,"payload":payload})
        with sqlite3.connect(self.database) as c: c.execute("INSERT INTO phase6_results(study_id,candidate_id,result_type,payload_json,digest) VALUES(?,?,?,?,?)",(study_id,candidate_id,result_type,json.dumps(payload,sort_keys=True),value))
        return value
    def verdict(self, study_id: str, candidate_id: str, value: dict):
        vd=digest({"study_id":study_id,"candidate_id":candidate_id,"verdict":value})
        with sqlite3.connect(self.database) as c: c.execute("INSERT INTO phase6_verdicts VALUES(?,?,?,?,?,?)",(study_id,candidate_id,value["verdict"],json.dumps(value["reasons"],sort_keys=True),json.dumps(value,sort_keys=True),vd))
        return vd
    def study_result(self, study_id: str, result_type: str, payload: dict):
        value=digest({"study_id":study_id,"result_type":result_type,"payload":payload})
        with sqlite3.connect(self.database) as c:
            c.execute("INSERT INTO phase6_study_results(study_id,result_type,payload_json,digest,created_at) VALUES(?,?,?,?,?)",
              (study_id,result_type,json.dumps(payload,sort_keys=True),value,datetime.now(timezone.utc).isoformat()))
        return value
