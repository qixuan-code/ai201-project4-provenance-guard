"""
Standalone test for Signal 2 (burstiness) — no API key needed.
Also runs Signal 1 on the same inputs so you can compare where they agree/disagree.

Usage:
    GROQ_API_KEY=your_key python test_signal2.py

Signal 2 runs locally (pure heuristics), so its results appear immediately.
Signal 1 requires the Groq API.
"""

import os
from signals.burstiness import compute_burstiness_score

TESTS = [
    (
        "AI-like (uniform, formal)",
        (
            "Artificial intelligence represents a transformative technology that enables "
            "machines to perform tasks traditionally requiring human intelligence. "
            "These systems leverage advanced algorithms to process vast amounts of data. "
            "The applications span numerous industries including healthcare, finance, and education. "
            "Machine learning models are trained on large datasets to identify patterns. "
            "Natural language processing allows computers to understand human communication. "
            "Computer vision systems can analyse images with remarkable accuracy. "
            "Reinforcement learning enables agents to optimise behaviour through feedback. "
            "These capabilities continue to advance at an unprecedented rate."
        ),
    ),
    (
        "Human-like (bursty, personal)",
        (
            "I burned the toast again this morning. Third time this week. "
            "The smoke alarm went off and my neighbor texted asking if everything was okay — "
            "which, honestly, was a fair question given that I have now set off the alarm "
            "four times in the past month, twice during what I would describe as completely "
            "normal cooking activities that somehow still ended in disaster. "
            "She's very patient. "
            "I bought a new toaster. "
            "It has six settings. I use setting two. "
            "This morning I used setting three and apparently that was the setting that "
            "burns everything to a perfect carbon rectangle."
        ),
    ),
    (
        "Ambiguous (polished but human)",
        (
            "The methodology employed a mixed-methods research design. "
            "Quantitative data were collected via structured surveys administered to 412 participants. "
            "Qualitative data emerged from semi-structured interviews with a purposive subsample of 24. "
            "Survey responses were analysed using descriptive and inferential statistics. "
            "Interview transcripts underwent thematic analysis following Braun and Clarke. "
            "Triangulation of findings across methods strengthened the validity of conclusions. "
            "Limitations include potential self-selection bias in the survey sample. "
            "Future research should replicate these findings in cross-cultural contexts."
        ),
    ),
    (
        "Edge case: short text (< 8 sentences — expect neutral 0.5)",
        "The sun dipped below the horizon, painting the sky in hues of amber and rose. "
        "I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet.",
    ),
]


def bar(score: float, width: int = 30) -> str:
    """ASCII progress bar for a [0, 1] score."""
    filled = round(score * width)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {score:.3f}"


def run_signal2():
    print("=" * 65)
    print("SIGNAL 2 — Burstiness (no API needed)")
    print("  0.0 = uniform / AI-like   |   1.0 = bursty / human-like")
    print("=" * 65)

    for label, text in TESTS:
        raw, score = compute_burstiness_score(text)
        ai_s2 = 1.0 - score
        print(f"\n{label}")
        print(f"  burstiness_raw (B) : {raw:+.3f}")
        print(f"  burstiness_score   : {bar(score)}")
        print(f"  ai_s2 (1 - score)  : {bar(ai_s2)}")


def run_both():
    """
    Run both signals and show side-by-side comparison.
    Requires GROQ_API_KEY.
    """
    try:
        from signals.perplexity import compute_perplexity_score
    except Exception as e:
        print(f"\nCould not import Signal 1: {e}")
        return

    if not os.environ.get("GROQ_API_KEY"):
        print("\nGROQ_API_KEY not set — skipping Signal 1 comparison.")
        return

    print("\n" + "=" * 65)
    print("BOTH SIGNALS — side-by-side comparison")
    print("  ai_s1 from Signal 1 (LLM classification)")
    print("  ai_s2 from Signal 2 (burstiness heuristic)")
    print("  agreement = 1 - |ai_s1 - ai_s2|")
    print("=" * 65)

    for label, text in TESTS:
        s1_raw, s1_score = compute_perplexity_score(text)
        s2_raw, s2_score = compute_burstiness_score(text)

        ai_s1 = 1.0 - s1_score
        ai_s2 = 1.0 - s2_score
        disagreement = abs(ai_s1 - ai_s2)
        agreement = 1.0 - disagreement

        print(f"\n{label}")
        print(f"  ai_s1 (Signal 1) : {bar(ai_s1)}")
        print(f"  ai_s2 (Signal 2) : {bar(ai_s2)}")
        print(f"  disagreement     : {disagreement:.3f}  |  agreement: {agreement:.3f}")

        if disagreement > 0.4:
            print("  ⚠  High disagreement — aggregator will apply penalty")
        elif disagreement < 0.15:
            print("  ✓  Signals agree")
        else:
            print("  ~  Moderate disagreement")


if __name__ == "__main__":
    run_signal2()
    run_both()
