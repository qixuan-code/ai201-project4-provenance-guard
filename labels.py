"""
Transparency label variants — planning.md §3.

Label text is defined here as constants so the exact wording is easy to
audit, update, and test independently of routing logic.

Variant selection thresholds (planning.md §2):
    confidence >= 0.80          → LABEL_AI
    confidence  0.65 – 0.79     → LABEL_UNCERTAIN_LEANING_AI
    confidence  0.40 – 0.64     → LABEL_UNCERTAIN_NO_SIGNAL
    confidence <= 0.39          → LABEL_HUMAN

TransparencyLabel fields:
    variant             "ai" | "human" | "uncertain"
    headline            short display title
    body                explanatory paragraph (non-technical reader)
    confidence_display  human-readable certainty phrase
    appeal_prompt       invitation to appeal, or None (human variant)
"""

from typing import Optional, TypedDict


class TransparencyLabel(TypedDict):
    variant: str            # "ai" | "human" | "uncertain"
    headline: str
    body: str
    confidence_display: str
    appeal_prompt: Optional[str]


LABEL_AI: TransparencyLabel = {
    "variant": "ai",
    "headline": "Likely AI-generated",
    "body": (
        "Our system found patterns consistent with AI-generated text — specifically, "
        "predictable word choices and unusually uniform sentence rhythm. "
        "These are statistical tendencies, not proof of authorship."
    ),
    "confidence_display": "High confidence",
    "appeal_prompt": (
        "If you wrote this yourself, share the context below and we'll review it."
    ),
}

LABEL_HUMAN: TransparencyLabel = {
    "variant": "human",
    "headline": "Likely written by a person",
    "body": (
        "Our system found patterns consistent with human authorship — varied word choices "
        "and irregular sentence rhythm that differ from what we typically see in "
        "AI-generated text."
    ),
    "confidence_display": "High confidence",
    "appeal_prompt": None,  # No appeal warranted on a positive result (planning.md §3)
}

# Uncertain band split into two sub-variants (planning.md §3):
# 3a — leaning AI but below the high-confidence threshold
LABEL_UNCERTAIN_LEANING_AI: TransparencyLabel = {
    "variant": "uncertain",
    "headline": "Origin unclear — some AI patterns detected",
    "body": (
        "Our system found some patterns associated with AI-generated text, but the "
        "signal is mixed and not strong enough for a firm conclusion. "
        "This is a preliminary flag, not a finding. Polished or formally written prose, "
        "short texts, and certain genres often produce this result."
    ),
    "confidence_display": "Uncertain — leaning AI",
    "appeal_prompt": (
        "If you wrote this yourself, share the context and we'll take another look."
    ),
}

# 3b — genuinely ambiguous, no clear signal either way
LABEL_UNCERTAIN_NO_SIGNAL: TransparencyLabel = {
    "variant": "uncertain",
    "headline": "Origin unclear",
    "body": (
        "Our system found no strong signal in either direction. This text did not show "
        "clear patterns of either human or AI authorship. "
        "This is not a flag — it means our tools don't have enough information "
        "to make a useful call."
    ),
    "confidence_display": "Uncertain — no clear signal",
    "appeal_prompt": "Something seem wrong? You can share context below.",
}


def get_label(confidence: float) -> TransparencyLabel:
    """
    Map a confidence score to the appropriate TransparencyLabel variant.

    Args:
        confidence: float ∈ [0.0, 1.0] — probability of AI authorship

    Returns:
        One of the four label constants above.
    """
    if confidence >= 0.80:
        return LABEL_AI
    elif confidence >= 0.65:
        return LABEL_UNCERTAIN_LEANING_AI
    elif confidence >= 0.40:
        return LABEL_UNCERTAIN_NO_SIGNAL
    else:
        return LABEL_HUMAN


def get_attribution(confidence: float) -> str:
    """
    Return the attribution string for a given confidence score.
    Used in the top-level response and audit log fields.

    Values: "likely_ai" | "uncertain" | "likely_human"
    """
    if confidence >= 0.80:
        return "likely_ai"
    elif confidence >= 0.40:
        return "uncertain"
    else:
        return "likely_human"
