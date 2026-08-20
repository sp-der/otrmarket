from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from src.research.replay.runs import canonical_json
from .schema import EXPERIMENT_SCHEMA_SQL


class ExperimentStore:
    """Research-only immutable definitions and append-only comparison results."""

    def __init__(self, database: str | Path):
        self.database = Path(database).resolve()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript(EXPERIMENT_SCHEMA_SQL)

    def create(self, definition: dict, candidates: list[dict]) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    definition["experiment_id"], definition["experiment_name"], definition["hypothesis"],
                    definition["baseline_run_id"], definition["created_at"], definition["git_commit"],
                    definition["data_capture_id"], canonical_json(definition["markets"]),
                    canonical_json(definition["contracts"]), definition["start_time"], definition["end_time"],
                    definition["replay_mode"], definition["fill_model"], definition["ambiguity_policy"],
                    canonical_json(definition["account_profile"]), canonical_json(definition["execution_config"]),
                    canonical_json(definition["baseline_configuration"]), definition["status"],
                    definition["data_quality_status"], definition["definition_digest"],
                ),
            )
            for candidate in candidates:
                connection.execute(
                    "INSERT INTO experiment_candidates VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        candidate["candidate_id"], definition["experiment_id"], candidate["candidate_name"],
                        candidate["run_id"], canonical_json(candidate["configuration"]),
                        canonical_json(candidate["configuration_diff"]), candidate["equivalence_status"],
                        candidate["created_at"], candidate["definition_digest"],
                    ),
                )

    def append_comparison(self, result: dict) -> int:
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """INSERT INTO experiment_comparisons(experiment_id,candidate_id,created_at,equivalence_status,
                first_divergence_json,baseline_digest,candidate_digest,comparison_digest) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    result["experiment_id"], result["candidate_id"], result["created_at"],
                    result["equivalence_status"], canonical_json(result["first_divergence"]),
                    result["digests"]["baseline"], result["digests"]["candidate"],
                    result["digests"]["comparison"],
                ),
            )
            comparison_id = cursor.lastrowid
            for match in result["setup_matches"]:
                connection.execute(
                    "INSERT INTO experiment_setup_matches(comparison_id,thesis_key,baseline_setup_id,candidate_setup_id,classification,matching_basis,matching_confidence,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                    (comparison_id, match["thesis_key"], match.get("baseline_setup_id"), match.get("candidate_setup_id"), match["classification"], match["matching_basis"], match["matching_confidence"], canonical_json(match)),
                )
            for metric, delta in result["metric_deltas"].items():
                connection.execute("INSERT INTO experiment_metric_deltas(comparison_id,metric,baseline_value,candidate_value,absolute_delta,percentage_delta) VALUES(?,?,?,?,?,?)", (comparison_id, metric, delta["baseline"], delta["candidate"], delta["absolute_delta"], delta["percentage_delta"]))
            for metric, delta in result["behavior_deltas"].items():
                connection.execute("INSERT INTO experiment_behavior_deltas(comparison_id,metric,baseline_value,candidate_value,absolute_delta) VALUES(?,?,?,?,?)", (comparison_id, metric, delta["baseline"], delta["candidate"], delta["absolute_delta"]))
            for dimension, segments in result["segment_deltas"].items():
                for segment, payload in segments.items():
                    connection.execute("INSERT INTO experiment_segment_deltas(comparison_id,dimension,segment,payload_json) VALUES(?,?,?,?)", (comparison_id, dimension, segment, canonical_json(payload)))
            for timeframe, payload in result["timeframe_analysis"].items():
                connection.execute("INSERT INTO experiment_timeframe_analysis(comparison_id,timeframe,payload_json) VALUES(?,?,?)", (comparison_id, timeframe, canonical_json(payload)))
            verdict = result["verdict"]
            connection.execute("INSERT INTO experiment_verdicts(comparison_id,verdict,reasons_json,sample_counts_json,verdict_digest) VALUES(?,?,?,?,?)", (comparison_id, verdict["verdict"], canonical_json(verdict["reasons"]), canonical_json(verdict["sample_counts"]), result["digests"]["verdict"]))
            return int(comparison_id)

    @staticmethod
    def decode_row(row: sqlite3.Row) -> dict:
        item = dict(row)
        for key in tuple(item):
            if key.endswith("_json"):
                item[key[:-5]] = json.loads(item.pop(key) or "{}")
        return item
