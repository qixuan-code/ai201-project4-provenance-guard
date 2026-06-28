"""
Signal 1: LLM-based authorship classification via Groq.

Sends text to llama-3.1-8b-instant with a prompt that includes concrete score
anchors and a list of specific AI/human markers. Without anchors the model
regresses toward 0.5 on everything — the anchors force it to use the full
range when the evidence is clear.

Return contract (matches planning.md §1 and audit log schema):
    signal1_raw:   AI probability from LLM ∈ [0.0, 1.0] — stored in audit log
    signal1_score: 1 - signal1_raw ∈ [0.0, 1.0]
                   0.0 = AI-like, 1.0 = human-like
                   AI contribution to aggregator: ai_s1 = signal1_raw

Sentinel: (0.0, 0.5) returned when the response can't be parsed.
"""

import json
import os
import re
from functools import lru_cache

from groq import Groq

_MAX_CHARS: int = 3_000

_SYSTEM_PROMPT = """\
You are an expert AI content detector. Your job is to estimate the probability \
that a given text was written by an AI (vs. a human).

Use this scoring guide and be decisive — do NOT cluster around 0.5 unless \
the text is genuinely ambiguous:

0.80 – 1.00  CLEARLY AI
  • Transitional filler: "furthermore", "additionally", "it is important to note",
    "it is worth noting", "in conclusion", "it should be noted"
  • Vague corporate/academic language: "stakeholders", "various sectors",
    "responsible deployment", "numerous benefits", "transformative paradigm"
  • No personal voice, no specific anecdotes, no genuine opinions
  • All sentences similar in length and structure

0.55 – 0.79  LIKELY AI
  • Some AI-typical phrasing but not overwhelming
  • Structured and uniform without being blatant
  • May include one or two human touches

0.30 – 0.54  UNCERTAIN / MIXED
  • Formal human writing (academic, legal, journalistic) — structured but genuine
  • Lightly edited AI — has some human markers added
  • Truly ambiguous — could be either

0.10 – 0.29  LIKELY HUMAN
  • Personal voice, specific details, genuine opinions
  • Occasional grammatical informality or casual phrasing

0.00 – 0.09  CLEARLY HUMAN
  • Slang, contractions, lowercase, ALL CAPS emphasis
  • Typos, emotional language, "honestly?", stream-of-consciousness
  • Very specific personal anecdotes

Respond with valid JSON only — no markdown, no text outside the JSON:
{"ai_probability": 0.85, "reasoning": "one sentence explaining the strongest signal"}\
"""


@lru_cache(maxsize=1)
def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set")
    return Groq(api_key=api_key)


def _extract_json(content: str) -> dict:
    content = re.sub(r"```(?:json)?\s*", "", content).strip()
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(content)


def compute_perplexity_score(text: str) -> tuple:
    """
    Args:
        text: raw submission text

    Returns:
        (signal1_raw, signal1_score)
        signal1_raw   — AI probability ∈ [0.0, 1.0]; stored in audit log as llm_score
        signal1_score — 1 - signal1_raw; 0.0 = AI-like, 1.0 = human-like

    Returns (0.0, 0.5) on parse failure — treated as neutral by aggregator.
    """
    truncated = text[:_MAX_CHARS]
    client = _get_client()

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": truncated},
        ],
        temperature=0,
        max_tokens=120,
    )

    content = response.choices[0].message.content or ""

    try:
        parsed = _extract_json(content)
        ai_probability = float(parsed["ai_probability"])
        ai_probability = max(0.0, min(1.0, ai_probability))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0.0, 0.5

    return ai_probability, 1.0 - ai_probability
