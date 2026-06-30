"""
Verify label generation against planning.md §2 thresholds.
No API key or Flask needed — runs entirely locally.

Usage:
    python test_labels.py
"""

from labels import get_label, get_attribution, LABEL_AI, LABEL_HUMAN, \
    LABEL_UNCERTAIN_LEANING_AI, LABEL_UNCERTAIN_NO_SIGNAL


# ── Boundary scores that must hit each variant ────────────────────────────────
CASES = [
    # (score, expected_variant, expected_attribution, description)
    (0.95, "ai",        "likely_ai",    "well above AI threshold"),
    (0.80, "ai",        "likely_ai",    "exactly at AI threshold"),
    (0.79, "uncertain", "uncertain",    "just below AI threshold → leaning AI"),
    (0.65, "uncertain", "uncertain",    "bottom of leaning-AI band"),
    (0.64, "uncertain", "uncertain",    "top of no-signal band"),
    (0.40, "uncertain", "uncertain",    "bottom of no-signal band"),
    (0.39, "human",     "likely_human", "just below uncertain → human"),
    (0.10, "human",     "likely_human", "clearly human"),
    (0.00, "human",     "likely_human", "floor"),
]


def run():
    passed = 0
    failed = 0

    print(f"\n{'Score':>6}  {'Expected':>12}  {'Got':>12}  {'Headline'}")
    print("-" * 72)

    for score, exp_variant, exp_attr, desc in CASES:
        label = get_label(score)
        attr  = get_attribution(score)

        variant_ok = label["variant"] == exp_variant
        attr_ok    = attr == exp_attr

        status = "✓" if (variant_ok and attr_ok) else "✗"
        if variant_ok and attr_ok:
            passed += 1
        else:
            failed += 1

        print(f"{score:>6.2f}  {exp_variant:>12}  {label['variant']:>12}  "
              f"{status}  {label['headline'][:40]}")

    print(f"\n{passed}/{passed+failed} passed")

    # ── Spot-check label text matches planning.md §3 exactly ─────────────────
    print("\n── Label text spot-check ────────────────────────────────────────────")

    checks = [
        (LABEL_AI,                  "Likely AI-generated",                      "High confidence"),
        (LABEL_HUMAN,               "Likely written by a person",               "High confidence"),
        (LABEL_UNCERTAIN_LEANING_AI,"Origin unclear — some AI patterns detected","Uncertain — leaning AI"),
        (LABEL_UNCERTAIN_NO_SIGNAL, "Origin unclear",                           "Uncertain — no clear signal"),
    ]

    for label, exp_headline, exp_display in checks:
        h_ok = label["headline"] == exp_headline
        d_ok = label["confidence_display"] == exp_display
        status = "✓" if (h_ok and d_ok) else "✗"
        print(f"  {status}  variant={label['variant']!r:10}  "
              f"headline={label['headline'][:42]!r}")
        if not h_ok:
            print(f"       headline mismatch — expected {exp_headline!r}")
        if not d_ok:
            print(f"       display mismatch  — expected {exp_display!r}, got {label['confidence_display']!r}")

    # ── Confirm appeal_prompt is None only for human variant ─────────────────
    print("\n── appeal_prompt check ──────────────────────────────────────────────")
    for label in [LABEL_AI, LABEL_HUMAN, LABEL_UNCERTAIN_LEANING_AI, LABEL_UNCERTAIN_NO_SIGNAL]:
        has_prompt = label["appeal_prompt"] is not None
        expected   = label["variant"] != "human"  # human variant should have None
        status = "✓" if has_prompt == expected else "✗"
        print(f"  {status}  variant={label['variant']!r:10}  "
              f"appeal_prompt={'set' if has_prompt else 'None'}")


if __name__ == "__main__":
    run()
