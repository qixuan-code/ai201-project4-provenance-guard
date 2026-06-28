"""Quick smoke test for Signal 1 (perplexity). Run with:
    GROQ_API_KEY=your_key python test_signal1.py
"""
from signals.perplexity import compute_perplexity_score

tests = [
    ("AI-like",   "Artificial intelligence represents a transformative technology that enables machines to perform tasks that traditionally required human intelligence."),
    ("Human-like","I burned the toast again this morning. Third time this week. The smoke alarm went off and my neighbor texted asking if everything was okay."),
    ("Ambiguous", "The results were analyzed using a mixed-methods approach combining quantitative survey data with qualitative interview responses."),
]

for label, text in tests:
    raw, score = compute_perplexity_score(text)
    ai_s1 = 1 - score
    print(f"{label}")
    print(f"  perplexity_raw={raw:.2f}  perplexity_score={score:.3f}  ai_s1={ai_s1:.3f}")
    print()
