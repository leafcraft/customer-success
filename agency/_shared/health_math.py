"""Deterministic health-score math + band derivation.

This is the part of the health verdict that must NOT be left to the LLM:
band thresholds, the low-confidence safety floor, and the clamp ranges
are policy a VP of CS sets, not judgment a model improvises. The
health_score_agent's @chain calls these to enforce a floor the LLM
cannot waive — the LLM may explain and weigh the factors, but the band
and the safety overrides are decided here.

Pure functions, no I/O — trivially unit-testable.
"""
from __future__ import annotations

from typing import Any

# Band thresholds — inclusive lower bounds. Single source of truth; the
# prompt quotes these but the @chain enforces them.
BAND_HEALTHY = 80
BAND_WATCH = 60
BAND_AT_RISK = 40

# Below this confidence a "healthy" call is downgraded to "watch" — a
# low-confidence healthy verdict masks brewing churn.
SAFETY_CONFIDENCE_FLOOR = 0.6

# A score with no defensible explanation is indefensible; cap confidence.
INDEFENSIBLE_CONFIDENCE_CAP = 0.3


def clamp(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def band_for(score: float) -> str:
    """Derive the risk band from a (clamped) health score."""
    if score >= BAND_HEALTHY:
        return "healthy"
    if score >= BAND_WATCH:
        return "watch"
    if score >= BAND_AT_RISK:
        return "at_risk"
    return "critical"


def signals_to_score(signals: list[dict[str, Any]], usage: dict[str, Any]) -> dict[str, Any]:
    """A deterministic baseline health computation over the rolling signal
    window + usage snapshot. Used as the dev-store baseline the LLM refines
    and as the floor the @chain falls back to when the LLM gives nothing.

    Returns {health_score, score_delta_30d, confidence}. Heuristic but
    stable: start at 75, penalise decay / spikes / commercial friction,
    reward adoption. Confidence scales with how much signal we have.
    """
    signals = signals or []
    usage = usage or {}
    score = 75.0

    # Engagement: the DAU trend is the strongest leading indicator.
    dau_delta = usage.get("dau_delta_pct")
    if isinstance(dau_delta, (int, float)):
        score += float(dau_delta) * 60.0   # -0.42 → -25.2

    # Each negative-valued signal in the window drags the score.
    neg = 0
    for s in signals:
        v = s.get("value")
        if isinstance(v, (int, float)) and v < 0:
            score -= min(8.0, abs(float(v)) * 12.0)
            neg += 1
        if s.get("type") in ("ticket_spike", "champion_left", "email_bounce", "failed_payment"):
            score -= 6.0

    # Adoption rewards.
    seat = usage.get("seat_utilisation")
    if isinstance(seat, (int, float)):
        score += (float(seat) - 0.5) * 10.0
    nps = usage.get("nps")
    if isinstance(nps, (int, float)):
        score += (float(nps) - 7.0) * 2.0

    score = clamp(score, 0, 100, default=50.0)

    # Velocity: longer windows with more decline → larger negative delta.
    score_delta_30d = clamp(
        (dau_delta * 50.0) if isinstance(dau_delta, (int, float)) else -3.0 * neg,
        -100, 100, default=0.0,
    )

    # Confidence scales with signal count; thin history is low-confidence.
    n = len(signals)
    confidence = clamp(0.3 + 0.12 * n, 0, 0.95, default=0.3)
    if n < 7:
        # Rubric minimum lookback — defer to a CSM rather than guess.
        confidence = min(confidence, 0.6)

    return {
        "health_score": round(score, 1),
        "score_delta_30d": round(score_delta_30d, 1),
        "confidence": round(confidence, 2),
    }


def enforce_floor(health_score: Any, score_delta_30d: Any, confidence: Any,
                  has_explanation: bool) -> dict[str, Any]:
    """Apply the deterministic band + safety + quality floors.

    Returns the corrected {health_score, score_delta_30d, risk_band,
    confidence}. The LLM cannot waive any of these — they are policy.
    """
    score = clamp(health_score, 0, 100, default=50.0)
    delta = clamp(score_delta_30d, -100, 100, default=0.0)
    conf = clamp(confidence, 0, 1, default=0.3)

    # QUALITY FLOOR: an unexplained score is indefensible.
    if not has_explanation:
        conf = min(conf, INDEFENSIBLE_CONFIDENCE_CAP)

    band = band_for(score)

    # SAFETY FLOOR: a low-confidence "healthy" call is downgraded.
    if band == "healthy" and conf < SAFETY_CONFIDENCE_FLOOR:
        band = "watch"

    return {
        "health_score": round(score, 1),
        "score_delta_30d": round(delta, 1),
        "risk_band": band,
        "confidence": round(conf, 2),
    }
