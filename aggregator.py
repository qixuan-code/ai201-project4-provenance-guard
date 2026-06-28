"""
Confidence Aggregator — planning.md §1 "Combining Signals".

Takes ai_s1 (Signal 1 AI likelihood) and ai_s2 (Signal 2 AI likelihood),
both ∈ [0.0, 1.0] where 1.0 = maximally AI-like, and produces a single
calibrated confidence score.

Three-step formula:

  Step 1 — Weighted average (Signal 1 weighted higher: more grounded in LM behaviour)
    base = 0.6 * ai_s1 + 0.4 * ai_s2

  Step 2 — Disagreement penalty (pulls score toward uncertainty when signals diverge)
    disagreement = |ai_s1 - ai_s2|
    penalized = base * (1 - 0.3 * disagreement)
    Maximum penalty at disagreement=1.0: penalized = base * 0.7
    → opposing signals can never produce confidence > 0.7 (below the 0.80 AI threshold)

  Step 3 — Overlap region cap
    When both signals are low (ai_s1 < 0.35 AND ai_s2 < 0.35), the text sits in
    the region where polished human writing is indistinguishable from AI output.
    Cap confidence at 0.72 regardless of other values.

Short-text guard (planning.md §5 edge case 1):
    Texts under ~150 words lack enough sentences for Signal 2 to be reliable.
    Add 0.12 to effective disagreement before applying the penalty, nudging
    short texts toward the uncertain band.

Sentinel handling:
    Either signal may return its neutral sentinel (0.5 score from a failed call).
    The aggregator treats these normally — a 0.5 contributes as a weak signal
    rather than crashing the pipeline.
"""


# Aggregator constants — must match planning.md §1 exactly
_W1 = 0.6        # Signal 1 weight
_W2 = 0.4        # Signal 2 weight
_PENALTY = 0.3   # disagreement penalty multiplier
_SHORT_TEXT_BONUS = 0.12   # extra disagreement for texts < _SHORT_WORD_THRESHOLD words
_SHORT_WORD_THRESHOLD = 150
_OVERLAP_CAP = 0.72        # cap for the low-overlap region
_OVERLAP_THRESHOLD = 0.35  # both signals below this → overlap region


def aggregate_confidence(
    ai_s1: float,
    ai_s2: float,
    word_count: int,
) -> dict:
    """
    Combine two signal scores into a calibrated confidence score.

    Args:
        ai_s1:       Signal 1 AI likelihood ∈ [0.0, 1.0]
        ai_s2:       Signal 2 AI likelihood ∈ [0.0, 1.0]
        word_count:  approximate word count of the original text

    Returns dict with:
        confidence:        float ∈ [0.0, 1.0] — final calibrated score
        base:              weighted average before penalties
        disagreement:      |ai_s1 - ai_s2| (raw, before short-text bonus)
        effective_disagreement: disagreement used in penalty (may include bonus)
        overlap_cap_applied: bool — whether the 0.72 cap fired
        short_text:        bool — whether the short-text bonus was applied
    """
    # ── Step 1: weighted base ────────────────────────────────────────────────
    base = _W1 * ai_s1 + _W2 * ai_s2

    # ── Short-text guard ─────────────────────────────────────────────────────
    short_text = word_count < _SHORT_WORD_THRESHOLD
    disagreement = abs(ai_s1 - ai_s2)
    effective_disagreement = min(1.0, disagreement + (_SHORT_TEXT_BONUS if short_text else 0.0))

    # ── Step 2: disagreement penalty ─────────────────────────────────────────
    penalized = base * (1.0 - _PENALTY * effective_disagreement)

    # ── Step 3: overlap region cap ────────────────────────────────────────────
    overlap_cap_applied = ai_s1 < _OVERLAP_THRESHOLD and ai_s2 < _OVERLAP_THRESHOLD
    if overlap_cap_applied:
        penalized = min(penalized, _OVERLAP_CAP)

    confidence = max(0.0, min(1.0, penalized))

    return {
        "confidence": confidence,
        "base": round(base, 4),
        "disagreement": round(disagreement, 4),
        "effective_disagreement": round(effective_disagreement, 4),
        "overlap_cap_applied": overlap_cap_applied,
        "short_text": short_text,
    }
