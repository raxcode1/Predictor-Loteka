from __future__ import annotations

import copy

import pytest

from quant import BASELINE
from quant.decision import (
    MODE_LIVE,
    MODE_PAPER,
    STATUS_CANDIDATE,
    STATUS_NO_EDGE,
    RiskLimits,
    decide,
    kelly_fraction,
    m4_gate,
    trading_mode,
)
from quant.integration import attach, gate_alert


def strong_report(hits: int = 1400, n: int = 10000) -> dict:
    return {
        "generated_at": "2026-11-08T00:00:00+00:00",
        "dataset": {"sha256": "abc", "last_draw": 10},
        "walk_forward": {
            "selected_model": {"name": "markov1(a=10,w=all)"},
            "test_calibrated": {"n": n, "ece_top_label": 0.005},
            "test_raw": {"n": n, "top1_hits": hits},
            "diebold_mariano_calibrated": {"p_value": 1e-9},
            "blocks_beating_uniform": 5,
        },
    }


def test_trading_mode_defaults_to_paper():
    assert trading_mode({}) == MODE_PAPER
    assert trading_mode({"RULETA_TRADING_MODE": "LIVE"}) == MODE_LIVE
    assert trading_mode({"RULETA_TRADING_MODE": "yes"}) == MODE_PAPER


def test_empty_report_abstains():
    decision = decide({}, mode=MODE_LIVE)
    assert decision["status"] == STATUS_NO_EDGE
    assert decision["signals_allowed"] is False
    assert decision["recommended_stake_fraction"] == 0.0
    assert decision["reasons"]


def test_strong_evidence_still_paper_by_default():
    decision = decide(strong_report(), mode=MODE_PAPER)
    assert decision["status"] == STATUS_CANDIDATE
    assert decision["signals_allowed"] is False
    assert decision["recommended_stake_fraction"] == 0.0
    assert decision["paper_stake_fraction"] > 0
    assert any("paper" in r for r in decision["reasons"])


def test_strong_evidence_in_live_mode_respects_stake_cap():
    decision = decide(strong_report(), mode=MODE_LIVE, limits=RiskLimits(max_stake_fraction=0.005))
    assert decision["signals_allowed"] is True
    assert 0 < decision["recommended_stake_fraction"] <= 0.005


@pytest.mark.parametrize(
    "path,value",
    [
        (("walk_forward", "selected_model", "name"), "uniform"),
        (("walk_forward", "diebold_mariano_calibrated", "p_value"), 0.2),
        (("walk_forward", "blocks_beating_uniform"), 2),
        (("walk_forward", "test_calibrated", "ece_top_label"), 0.1),
        (("walk_forward", "test_raw", "top1_hits"), 540),  # 5.4%: no supera el break-even con prior escéptico
    ],
)
def test_any_failed_check_abstains(path, value):
    report = copy.deepcopy(strong_report())
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    decision = decide(report, mode=MODE_LIVE)
    assert decision["status"] == STATUS_NO_EDGE
    assert decision["signals_allowed"] is False


def test_kelly_fraction_zero_without_edge():
    assert kelly_fraction(BASELINE, 18.0, RiskLimits()) == 0.0
    assert kelly_fraction(0.04, 18.0, RiskLimits()) == 0.0


def test_risk_limits():
    limits = RiskLimits(max_daily_loss_fraction=0.05, max_bets_per_day=3)
    assert limits.allows(-0.01, 1) == (True, None)
    assert limits.allows(-0.06, 0)[0] is False
    assert limits.allows(0.0, 3)[0] is False


def test_m4_gate_requires_corrected_evidence():
    small = m4_gate({"alerts": 80, "alert_hits": 11, "window_rounds": 120, "window_hits": 12,
                     "anchor_alerts": 20, "anchor_hits": 3})
    assert small["passed"] is False
    assert small["anchor_scales_stake"] is False
    assert any("alertas resueltas" in r for r in small["reasons"])
    holm = [c["p_value_holm"] for c in small["cohorts"]]
    raw = [c["p_value"] for c in small["cohorts"]]
    assert all(h >= r for h, r in zip(holm, raw))


def test_gate_alert_blocks_in_paper_mode():
    alert = {"fired": True, "stake_applied": True, "stake_fraction": 0.02, "stake_amount": 20.0}
    gated = gate_alert(alert, {"trading_mode": MODE_PAPER}, {"passed": True, "reasons": []})
    assert gated["fired"] is False and gated["rule_fired"] is True
    assert gated["stake_applied"] is False and gated["stake_amount"] == 0.0
    assert gated["state"] == STATUS_NO_EDGE
    assert "modo paper-trading" in gated["block_reasons"]
    assert alert["fired"] is True  # no se muta el original


def test_attach_without_report_is_safe(monkeypatch):
    monkeypatch.delenv("RULETA_TRADING_MODE", raising=False)
    payload = {"alert": {"fired": True, "stake_applied": True}, "alert_stats": {}}
    out = attach(payload, summary=None, error="sin datos")
    assert out["decision"]["status"] == STATUS_NO_EDGE
    assert out["alert"]["fired"] is False
    assert out["api_version"] == 2
    assert out["quant_error"] == "sin datos"
