from __future__ import annotations

import json
import unittest

from src.integrations.vibe_research.presentation import (
    extract_research_result,
    normalize_vibe_snapshot,
)


class VibeResearchPresentationTests(unittest.TestCase):
    def test_extracts_machine_readable_analysis_from_cli_content(self):
        analysis = {
            "classification": "STRATEGY",
            "summary": "Candidate quality was acceptable but the move invalidated the thesis.",
            "evidence": ["Nautilus outcome matched"],
            "hypotheses": [],
            "experiments": [],
            "evidence_gaps": [],
            "confidence": 0.72,
            "promotion_recommendation": "COLLECT_MORE",
        }
        envelope = {
            "status": "success",
            "run_id": "run_123",
            "run_dir": "/tmp/run_123",
            "content": json.dumps(analysis),
        }

        self.assertEqual(extract_research_result(envelope), analysis)

    def test_extracts_json_from_fenced_content(self):
        payload = {
            "classification": "EXECUTION",
            "summary": "Fill path differed while the directional outcome matched.",
        }
        wrapped = {"content": "```json\n" + json.dumps(payload) + "\n```"}
        self.assertEqual(extract_research_result(wrapped), payload)

    def test_snapshot_preserves_transport_metadata(self):
        analysis = {
            "classification": "MARKET",
            "summary": "Collect more comparable samples.",
            "promotion_recommendation": "COLLECT_MORE",
        }
        snapshot = {
            "recent_findings": [
                {
                    "setup_id": "abc123",
                    "result": {
                        "status": "success",
                        "run_id": "run_abc",
                        "content": json.dumps(analysis),
                    },
                }
            ]
        }

        normalized = normalize_vibe_snapshot(snapshot)
        finding = normalized["recent_findings"][0]
        self.assertTrue(finding["analysis_extracted"])
        self.assertEqual(finding["result"], analysis)
        self.assertEqual(finding["transport_result"]["run_id"], "run_abc")
        self.assertNotIn("transport_result", snapshot["recent_findings"][0])


if __name__ == "__main__":
    unittest.main()
