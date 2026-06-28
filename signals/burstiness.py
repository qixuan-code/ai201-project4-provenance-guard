"""
Signal 2: Burstiness / Stylometric Variance (planning.md §1, Signal 2).

Four metrics combined into a single burstiness_score ∈ [0, 1]:

  Metric 1 — Burstiness coefficient B = (σ - μ) / (σ + μ)     [sentence-level]
    Requires ≥ _MIN_SENTENCES sentences. Captures irregular rhythm.
    B ∈ (-1, 1): -1 = all same length (AI-like); near 1 = high variance (human-like)

  Metric 2 — Sentence length range ratio                        [sentence-level]
    (max - min) / max. Captures extreme contrasts like a 1-word sentence
    next to a 25-word one — a human rhetorical move AI avoids.

  Metric 3 — Average word length (inverted)                     [word-level, always runs]
    AI writing favours polysyllabic formal words ("transformative", "stakeholders").
    Human casual writing uses shorter words ("ok", "yeah", "fine").
    Score: 1.0 = short words (human-like), 0.0 = long words (AI-like).

  Metric 4 — Informal language markers                          [word-level, always runs]
    Contractions (won't, I've), ALL CAPS emphasis, casual discourse markers
    (honestly, basically, ok, so). Score: 1.0 = casual (human-like), 0.0 = formal.

Blending strategy:
  - Long texts (≥ _MIN_SENTENCES): all 4 metrics (sentence-level metrics carry more weight)
  - Short texts (<  _MIN_SENTENCES): only metrics 3 & 4 (word-level only)
  This replaces the old hard 0.5 sentinel for short texts with a real score.

Return contract:
    burstiness_raw:   B coefficient ∈ (-1, 1), or 0.0 if text too short for sentence analysis
    burstiness_score: blended score ∈ [0.0, 1.0]
                      0.0 = uniform / formal (AI-like)
                      1.0 = bursty / casual (human-like)
                      AI contribution: ai_s2 = 1 - burstiness_score
"""

import re
import statistics

_MIN_SENTENCES = 3   # reduced from 8 — even 3 sentences give useful variance signal


# ── Sentence tokenisation ────────────────────────────────────────────────────

def _split_sentences(text: str) -> list:
    raw = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in raw if s.strip()]


def _word_count(sentence: str) -> int:
    return len(sentence.split())


# ── Metric 1: Burstiness coefficient ─────────────────────────────────────────

def _burstiness_coefficient(lengths: list) -> float:
    if len(lengths) < 2:
        return 0.0
    mu = statistics.mean(lengths)
    sigma = statistics.pstdev(lengths)
    denom = sigma + mu
    if denom == 0:
        return 0.0
    return (sigma - mu) / denom


def _rescale_B(B: float) -> float:
    """Map B from (-1, 1) to [0, 1]. B=-1 → 0.0 (AI-like), B=1 → 1.0 (human-like)."""
    return (B + 1.0) / 2.0


# ── Metric 2: Sentence length range ratio ────────────────────────────────────

def _range_ratio(lengths: list) -> float:
    if not lengths or max(lengths) == 0:
        return 0.0
    return (max(lengths) - min(lengths)) / max(lengths)


# ── Metric 3: Average word length (inverted) ─────────────────────────────────

def _word_length_score(text: str) -> float:
    """
    Short avg word length → human-like (score near 1.0).
    Long avg word length → AI-like (score near 0.0).
    Calibrated: 3 chars/word = fully human, 8 chars/word = fully AI.
    """
    words = [w.strip('.,!?;:"\'()') for w in text.split() if w.strip('.,!?;:"\'()')]
    if not words:
        return 0.5
    avg_len = sum(len(w) for w in words) / len(words)
    score = 1.0 - (avg_len - 3.0) / (8.0 - 3.0)
    return max(0.0, min(1.0, score))


# ── Metric 4: Informal language markers ──────────────────────────────────────

_CONTRACTIONS = {
    "won't", "can't", "i've", "i'm", "i'll", "i'd", "don't", "doesn't",
    "didn't", "isn't", "aren't", "wasn't", "weren't", "haven't", "hadn't",
    "wouldn't", "couldn't", "shouldn't", "it's", "that's", "there's",
    "they're", "they've", "we're", "we've", "you're", "you've", "he's",
    "she's", "who's", "what's", "where's", "let's",
}

_CASUAL_MARKERS = {
    "ok", "okay", "yeah", "yep", "nope", "honestly", "basically", "literally",
    "actually", "kinda", "gonna", "wanna", "gotta", "tbh", "lol", "omg",
    "ugh", "hmm", "haha", "wow", "well", "so", "anyway", "like",
}


def _informal_score(text: str) -> float:
    """
    Casual writing (contractions, slang, emphasis) → score near 1.0 (human-like).
    Formal writing → score near 0.0 (AI-like).
    Soft cap: 15% informal markers in a text = maximum score.
    """
    raw_words = text.split()
    if not raw_words:
        return 0.0

    normalised = [w.lower().strip('.,!?;:"\'()') for w in raw_words]

    contraction_count = sum(1 for w in normalised if w in _CONTRACTIONS)
    casual_count = sum(1 for w in normalised if w in _CASUAL_MARKERS)
    # ALL CAPS words ≥ 3 chars that aren't obvious acronyms (e.g. "WAY", "SO")
    caps_count = sum(1 for w in raw_words if len(w) >= 3 and w.isupper() and w.isalpha())

    ratio = (contraction_count + casual_count + caps_count) / len(raw_words)
    return min(1.0, ratio / 0.15)   # 15% informal = 1.0


# ── Public API ───────────────────────────────────────────────────────────────

def compute_burstiness_score(text: str) -> tuple:
    """
    Compute the burstiness signal for `text`.

    Returns:
        (burstiness_raw, burstiness_score)

        burstiness_raw   — B coefficient ∈ (-1, 1); 0.0 when text is too short
                           for sentence-level analysis
        burstiness_score — ∈ [0.0, 1.0]: 0=AI-like, 1=human-like
    """
    sentences = _split_sentences(text)

    # Word-level metrics always run (work on any text length)
    m_word_len = _word_length_score(text)
    m_informal = _informal_score(text)

    if len(sentences) >= _MIN_SENTENCES:
        lengths = [_word_count(s) for s in sentences]
        B = _burstiness_coefficient(lengths)
        m_burst = _rescale_B(B)
        m_range = _range_ratio(lengths)

        # Full blend — sentence metrics carry more weight when available
        score = (
            0.35 * m_burst
            + 0.20 * m_range
            + 0.30 * m_word_len
            + 0.15 * m_informal
        )
        return B, max(0.0, min(1.0, score))

    else:
        # Short text: word-level metrics only
        # B=0.0 indicates no sentence-level data (not a sentinel for "unavailable")
        score = 0.60 * m_word_len + 0.40 * m_informal
        return 0.0, max(0.0, min(1.0, score))
