# signals package — each module exposes one public function
# perplexity.py  → compute_perplexity_score(text) -> tuple[float, float]
# burstiness.py  → compute_burstiness_score(text) -> tuple[float, float]
#
# Both return (raw_value, normalized_score) where normalized_score ∈ [0, 1]:
#   0.0 = AI-like
#   1.0 = human-like
# AI contribution to aggregator: ai_s = 1 - normalized_score
