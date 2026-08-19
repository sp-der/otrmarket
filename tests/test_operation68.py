from types import SimpleNamespace

from src import main_68 as op68


def _setup(timeframe: str, strategy: str = "ICT_CONFLUENCE"):
    return SimpleNamespace(
        timeframe=timeframe,
        metadata={"strategy": strategy},
    )


def test_one_minute_candidates_are_scout_only(monkeypatch):
    monkeypatch.setattr(
        op68,
        "_previous_quality_gate_68",
        lambda connection, setup, histories=None: (True, "prior quality passed"),
    )
    setup = _setup("1m", "MSS_REVERSAL")

    allowed, reason = op68._adaptive_quality_gate_68(None, setup, {})

    assert allowed is False
    assert "scout" in reason.lower()
    assert setup.metadata["one_minute_firewall_68"]["autonomous_execution"] is False
    assert setup.metadata["one_minute_firewall_68"]["strategy"] == "MSS_REVERSAL"


def test_five_minute_candidates_keep_existing_quality_decision(monkeypatch):
    monkeypatch.setattr(
        op68,
        "_previous_quality_gate_68",
        lambda connection, setup, histories=None: (True, "prior quality passed"),
    )
    setup = _setup("5m")

    allowed, reason = op68._adaptive_quality_gate_68(None, setup, {})

    assert allowed is True
    assert reason == "prior quality passed"
    assert "one_minute_firewall_68" not in setup.metadata


def test_existing_rejections_are_preserved(monkeypatch):
    monkeypatch.setattr(
        op68,
        "_previous_quality_gate_68",
        lambda connection, setup, histories=None: (False, "existing gate blocked"),
    )
    setup = _setup("1m")

    allowed, reason = op68._adaptive_quality_gate_68(None, setup, {})

    assert allowed is False
    assert reason == "existing gate blocked"
    assert "one_minute_firewall_68" not in setup.metadata


def test_intrabar_acceleration_is_five_minute_only():
    assert op68.op65.INTRABAR_TIMEFRAMES == {"5m"}
