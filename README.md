# ProvenanceGuard

An API for AI-content attribution. Accepts text, runs two independent detection signals, returns a calibrated confidence score and a plain-language transparency label, and supports creator appeals.

---

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

GROQ_API_KEY=your_key flask run --port 5001
```

---

## API Reference

### `POST /submit`

**Request:**
```json
{ "text": "...", "creator_id": "..." }
```

**Response:**
```json
{
  "content_id": "uuid",
  "timestamp": "ISO 8601",
  "creator_id": "...",
  "attribution": "likely_ai | uncertain | likely_human",
  "confidence": 0.74,
  "signals": {
    "perplexity_raw": 0.95,
    "burstiness_score": 0.31,
    "ai_s1": 0.95,
    "ai_s2": 0.69,
    "disagreement": 0.26,
    "overlap_cap_applied": false,
    "short_text": true
  },
  "label": {
    "variant": "uncertain",
    "headline": "Origin unclear — some AI patterns detected",
    "body": "...",
    "confidence_display": "Uncertain — leaning AI",
    "appeal_prompt": "If you wrote this yourself, share the context and we'll take another look."
  }
}
```

Rate limits: 10/min · 100/hr · 500/day.

### `POST /appeal/<content_id>`

**Request:**
```json
{ "creator_reasoning": "...", "contact_email": "optional" }
```

**Response:**
```json
{ "appeal_id": "uuid", "content_id": "...", "status": "under_review", "appeal_timestamp": "ISO 8601" }
```

### `GET /log`

Query params: `status`, `limit` (default 50), `offset` (default 0).

---

## Architecture Overview

A submission travels through five stages:

**1 → Validation.** `app.py` checks that `text` and `creator_id` are present, that `text` is a non-empty string under 50,000 characters, and that the request is within rate limits. Nothing else runs until this passes.

**2 → Signal computation.** Two functions run independently with no shared state. `signals/perplexity.py` sends the text to a Groq-hosted LLM and gets back an AI-probability score. `signals/burstiness.py` computes stylometric metrics locally without any API call. Each returns a sub-score on [0, 1] where 1.0 = maximally AI-like.

**3 → Confidence aggregation.** `aggregator.py` combines the two sub-scores into a single confidence value. It applies a disagreement penalty when the signals contradict each other, an extra nudge toward uncertain for short texts, and an overlap cap for the signal region where formal human writing is indistinguishable from AI output.

**4 → Label selection.** `labels.py` maps the confidence score to one of four transparency label variants. The label contains human-readable text ready to display to a creator — no further formatting required.

**5 → Audit log + response.** `audit.py` appends a complete record to `audit_log.jsonl` before the HTTP response is sent. The response returns the `content_id`, `attribution`, `confidence`, raw signal values, and the full label object.

Appeals (via `POST /appeal`) write back into the audit log, updating `status` to `under_review` and attaching the creator's reasoning alongside the original decision. No re-classification occurs.

---

## Detection Signals

### Signal 1 — LLM Classification (`llm_score`)

**What it measures.** A Groq-hosted LLM (llama-3.1-8b-instant, temperature=0) reads the text and estimates the probability it was AI-generated, returning a float on [0.0, 1.0]. The system prompt includes explicit score anchors and named surface markers so the model uses the full range rather than collapsing toward 0.5: scores of 0.80–1.0 require specific features like "furthermore", "it is important to note", "stakeholders", "paradigm shift"; scores of 0.00–0.09 require slang, contractions, and personal voice.

**Why this signal.** LLMs have strong priors about what AI-generated text looks like from training on large corpora of both. A classification prompt leverages those priors without requiring token-level log-probabilities, which Groq's API does not expose.

**What it misses.** The signal measures register, not authorship. Academic writing, legal boilerplate, and formal ESL writing use the same surface vocabulary as AI output — "furthermore", "stakeholders", "it is important to note" are grammatically correct; they are not exclusively AI. A PhD student's introduction will score similarly to AI output. Conversely, AI prompted to write casually will score lower.

---

### Signal 2 — Burstiness / Stylometric Variance (`burstiness_score`)

**What it measures.** Four heuristic metrics combined into a single score on [0.0, 1.0] where 0.0 = AI-like and 1.0 = human-like:

1. **Burstiness coefficient** B = (σ − μ) / (σ + μ) on sentence lengths. Human writing is bursty — sentence lengths cluster then suddenly shift. AI writing tends toward uniform medium-length sentences.
2. **Sentence length range ratio** — (max − min) / max. Captures extreme contrasts (a one-word sentence next to a 25-word one) that humans use for rhetorical effect.
3. **Average word length (inverted)** — AI writing favours polysyllabic formal vocabulary. Calibrated to 3–8 characters; short words → human-like.
4. **Informal language markers** — contractions, ALL CAPS emphasis, casual discourse markers (honestly, basically, ok). Presence → human-like.

Texts with fewer than 3 sentences use only metrics 3 and 4. Longer texts use all four with sentence-level metrics weighted higher (0.35/0.20/0.30/0.15).

**Why this signal.** It is fully local (no API call) and measures structural properties independent of vocabulary. A text can fool Signal 1 by using formal register. To also fool Signal 2 it must simultaneously maintain uniform sentence rhythm and avoid all informal markers — a harder bar. When the signals disagree, that disagreement is itself logged as evidence of uncertainty.

**What it misses.** Edited prose — journalism, published essays, academic writing — has normalised extremes. Sentence variance is low by editorial design, not by AI generation. Poetry with formal structure (anaphora, fixed line lengths) also scores AI-like. Any non-English text gets a near-zero informal score because the marker list is English-only.

---

## Confidence Scoring

### Formula

Both signals produce an AI likelihood sub-score (`ai_s1`, `ai_s2`) on [0.0, 1.0].

**Step 1 — Weighted average** (Signal 1 weighted higher; it has broader coverage across text types):
```
base = 0.6 × ai_s1 + 0.4 × ai_s2
```

**Step 2 — Disagreement penalty** (signals contradicting each other is evidence of uncertainty):
```
disagreement = |ai_s1 − ai_s2|
penalized = base × (1 − 0.3 × disagreement)
```
At maximum disagreement, the penalty caps output at `base × 0.7`. This means two perfectly opposing signals can never produce confidence above ~0.70, keeping the result in the uncertain band. Reaching confidence ≥ 0.80 requires both signals to agree.

**Step 3 — Short-text guard** (texts under ~150 words have less evidence):
```
effective_disagreement = min(1.0, disagreement + 0.12)
```
A 0.12 bonus added to disagreement before the penalty nudges short texts toward uncertain.

**Step 4 — Overlap region cap** (formal writing zone):
If `ai_s1 < 0.35` AND `ai_s2 < 0.35`, confidence is capped at 0.72. This is the region where formal human writing and AI output are indistinguishable by the current signals; expressing high confidence here would be dishonest.

### Thresholds

| Confidence | Attribution | Label variant |
|---|---|---|
| ≥ 0.80 | `likely_ai` | "Likely AI-generated" |
| 0.65 – 0.79 | `uncertain` | "Origin unclear — some AI patterns detected" |
| 0.40 – 0.64 | `uncertain` | "Origin unclear" |
| ≤ 0.39 | `likely_human` | "Likely written by a person" |

The uncertain band is 40 points wide by design. A false positive — flagging a human as AI — is meaningfully worse than returning an uncertain label. The 0.80 threshold sits well above 0.5 to reflect this asymmetric cost.

### Validation: two real submissions

**High-confidence AI — `confidence: 0.9205`**

Input:
> "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment."

- `llm_score`: 0.95 — "it is important to note", "furthermore", "stakeholders", "responsible deployment" all flagged as strong AI markers
- `burstiness_score`: 0.0 — no informal markers, no sentence-level variance (text too short); word-length metrics returned AI-like
- `ai_s1`: 0.95 · `ai_s2`: 1.0 · `disagreement`: 0.05 — signals agree; penalty negligible
- **`confidence: 0.9205`** → `likely_ai` → **"Likely AI-generated"**

**High-confidence human — `confidence: 0.175`**

Input:
> "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably wont go back unless someone drags me there"

- `llm_score`: 0.09 — lowercase, "honestly?", "WAY" (ALL CAPS), casual phrasing, no transitional filler
- `burstiness_score`: 0.64 — high informality score, varied sentence lengths
- `ai_s1`: 0.09 · `ai_s2`: 0.36 · `disagreement`: 0.27 — moderate disagreement; penalty applied
- **`confidence: 0.175`** → `likely_human` → **"Likely written by a person"**

The 0.745-point gap between these cases shows the scoring produces meaningful variation. The disagreement penalty was the only moderating factor on the ramen example; the AI example needed no moderation because both signals agreed.

---

## Transparency Labels

The label returned by `/submit` is one of four variants based on confidence. The uncertain band is split into two sub-variants with different copy.

### Variant 1 — High-confidence AI (`confidence ≥ 0.80`)

```
headline:            "Likely AI-generated"
body:                "Our system found patterns consistent with AI-generated text —
                      specifically, predictable word choices and unusually uniform
                      sentence rhythm. These are statistical tendencies, not proof
                      of authorship."
confidence_display:  "High confidence"
appeal_prompt:       "If you wrote this yourself, share the context below and we'll
                      review it."
```

### Variant 2 — High-confidence Human (`confidence ≤ 0.39`)

```
headline:            "Likely written by a person"
body:                "Our system found patterns consistent with human authorship —
                      varied word choices and irregular sentence rhythm that differ
                      from what we typically see in AI-generated text."
confidence_display:  "High confidence"
appeal_prompt:       null
```

No appeal prompt is shown for this variant. A positive result (human) does not warrant contesting.

### Variant 3a — Uncertain, leaning AI (`confidence 0.65 – 0.79`)

```
headline:            "Origin unclear — some AI patterns detected"
body:                "Our system found some patterns associated with AI-generated text,
                      but the signal is mixed and not strong enough for a firm conclusion.
                      This is a preliminary flag, not a finding. Polished or formally
                      written prose, short texts, and certain genres often produce
                      this result."
confidence_display:  "Uncertain — leaning AI"
appeal_prompt:       "If you wrote this yourself, share the context and we'll take
                      another look."
```

### Variant 3b — Uncertain, no clear signal (`confidence 0.40 – 0.64`)

```
headline:            "Origin unclear"
body:                "Our system found no strong signal in either direction. This text
                      did not show clear patterns of either human or AI authorship.
                      This is not a flag — it means our tools don't have enough
                      information to make a useful call."
confidence_display:  "Uncertain — no clear signal"
appeal_prompt:       "Something seem wrong? You can share context below."
```

No variant uses the words "cheating", "plagiarism", "dishonest", or "fraud". Every AI-leaning variant frames the result as a statistical pattern, not a judgment about intent.

---

## Rate Limiting

Applied to `POST /submit` only.

| Limit | Value | Reasoning |
|---|---|---|
| Per minute | 10 | A human writer submitting their own work doesn't need more than 10 submissions per minute. Anything faster is a script probing the confidence threshold to find the boundary between label variants. |
| Per hour | 100 | Allows a platform doing legitimate batch upload without hitting a wall, while preventing sustained automated abuse within a session. |
| Per day | 500 | Hard daily cap against overnight scraping campaigns that stay under the per-minute limit. |

Window type: sliding window (not fixed bucket), preventing burst abuse at reset boundaries. Storage: in-memory — resets on server restart; a production deployment would use Redis.

Rate limit violations return HTTP 429 with a JSON error body.

---

## Known Limitations

**The system will likely misclassify formally written human text as AI-generated — and this is a direct consequence of how Signal 1 is designed.**

Signal 1's scoring prompt lists specific vocabulary as high-confidence AI markers: "furthermore", "it is important to note", "stakeholders", "responsible deployment", "paradigm shift". These were chosen because they appear frequently in AI-generated text. They also appear frequently in human-written academic papers, legal briefs, business reports, and the writing of people who learned formal English as a second language. The LLM prompt has no mechanism to distinguish an AI that reaches for "furthermore" as filler from a human who uses it because it is grammatically correct. The signal measures register, not authorship — and for formally written human text, those come apart.

Signal 2 compounds the problem in the same direction. The word-length metric scores long words as AI-like (calibrated to 3–8 characters), so "paradigm", "implications", and "transformative" all push `burstiness_score` toward 0. The informal-marker list is English casual markers only — contractions, "honestly", "lol", ALL CAPS. Any formal text, regardless of origin or language, returns near-zero on this metric.

When both signals agree — formal vocabulary, no casual markers — the disagreement penalty does not fire and provides no cushion. The overlap cap only activates when both signals point toward human (`ai_s1 < 0.35` AND `ai_s2 < 0.35`); it does nothing for the false-positive case where both signals point toward AI. A formally written human text can reach confidence 0.65–0.75, land in "uncertain, leaning AI", and have no mechanism in the pipeline to flag that this is likely wrong.

**Short texts have no sentence-level data.** Signal 2's burstiness coefficient and range-ratio metrics require at least 3 sentences. Below that, only word-length and informal-marker metrics run. A one-sentence formal statement has long words and no informal markers — it returns `burstiness_score` near 0.0 regardless of who wrote it.

**LLM consistency.** Signal 1 uses `temperature=0`, but Groq's inference may produce different scores on identical text across API calls. Signal 2 is fully deterministic.

---

## Spec Reflection

**Where the spec helped.** The planning.md section on uncertainty representation forced an upfront answer to the question: is a borderline score more dangerous as a false positive (human flagged as AI) or a false negative (AI missed)? Writing that answer down before touching code — false positive is worse — made it obvious that the "Likely AI-generated" threshold had to sit well above 0.5, and that the uncertain band should be wide enough to absorb the cases the system genuinely cannot call. Without that written commitment it would have been easy to default to a symmetric 0.5 threshold. The spec made the asymmetric cost explicit before it became a design constraint in the aggregator.

**Where implementation diverged.** The spec described Signal 1 as perplexity-based: send the text to a language model, collect token log-probabilities, compute surprisal, and interpret low perplexity as AI-like. That is the textbook approach and exactly what planning.md specified. In practice, the Groq API returns `groq.BadRequestError: logprobs not supported` for llama-3.1-8b-instant — the parameter is not implemented. The spec assumed log-probability access that the chosen inference provider does not expose.

The replacement was a structured classification prompt asking the model to return an `ai_probability` float with explicit score anchors. This is a weaker signal in one specific way: it measures whatever the model's prompt-following makes salient, not the model's own generation likelihood. The explicit score anchors (added after initial testing showed scores clustering in the 0.35–0.42 range) substitute a checklist of surface markers for true surprisal. The spec was right about what the signal should measure; it was wrong about what the API would allow.

---

## AI Usage

**Instance 1: Signal 1 — initial generation and complete rewrite.**

I gave the AI the detection signals section of planning.md and asked it to generate a Signal 1 function using the Groq API to measure AI likelihood via token log-probabilities. The AI produced a function that called the completions endpoint with `logprobs=True` and computed surprisal from the returned token probabilities. This is the correct textbook approach and matched the spec exactly.

Running it returned `groq.BadRequestError: logprobs not supported`. The code was technically correct but assumed an API capability that does not exist. I overrode the entire approach, directing the AI to rewrite Signal 1 as a structured classification prompt returning a JSON object with an `ai_probability` float. That rewrite introduced a second problem: without explicit score guidance, the model regressed to the mean and returned scores in the 0.35–0.42 range on all inputs. I then directed the AI to add five-tier score anchors to the system prompt with named surface markers at each tier. The final Signal 1 is two full revisions away from what was initially generated.

**Instance 2: Signal 2 — a sentinel that silenced the signal entirely.**

I gave the AI the burstiness section of planning.md and asked it to generate a stylometric variance function. The AI produced a function with `_MIN_SENTENCES = 8` as the sentence-level threshold, below which it returned a hard sentinel `(0.0, 0.5)` — the neutral midpoint. The reasoning was sound: too few sentences makes the variance estimate unreliable.

In practice, all of my test texts were under 8 sentences, so every Signal 2 result was exactly 0.5. The aggregator's disagreement penalty then fired on every submission (ai_s1 varied; ai_s2 was always 0.5), producing a narrow confidence band of 0.30–0.45 regardless of how clearly AI or human the input was. I identified the cause from the test output and directed the AI to replace the sentinel with word-level metrics — average word length (inverted) and informal-marker count — that work on any text length, and to lower the threshold to 3 sentences. I also specified the blend weights (0.60/0.40 for short texts, 0.35/0.20/0.30/0.15 for long) rather than accepting a second uncalibrated default, because the first set had already produced a flat signal.

**Instance 3: Appeals endpoint — field name inconsistency.**

I asked the AI to generate the `POST /appeal` endpoint together with the audit log update logic. The code worked, but the endpoint expected `creator_statement` in the request body while planning.md specified `creator_reasoning`. The mismatch only surfaced when I tested the endpoint using the spec's field name and got a validation error. The AI had defaulted to its own paraphrase of the field name rather than copying the spec verbatim. I directed it to accept both names for compatibility and to use `creator_reasoning` as the canonical name in the stored record.

---

## Audit Log

Every decision and every appeal is recorded in `audit_log.jsonl` — one JSON object per line, append-only.

| Field | Type | Description |
|---|---|---|
| `content_id` | string | UUID returned to the submitter |
| `creator_id` | string | Provided by the submitter |
| `timestamp` | string | ISO 8601 UTC |
| `attribution` | string | `likely_ai` \| `uncertain` \| `likely_human` |
| `confidence` | float | ∈ [0.0, 1.0] |
| `llm_score` | float | Signal 1 raw AI probability |
| `burstiness_score` | float | Signal 2 normalised score (0=AI-like, 1=human-like) |
| `status` | string | `classified` \| `under_review` \| `overturned` \| `upheld` |
| `appeal` | object\|null | Populated by `POST /appeal`; null if no appeal filed |

Sample entries:

```json
{"content_id": "0e74813b-...", "creator_id": "test-human", "timestamp": "2026-06-28T20:27:15+00:00", "attribution": "likely_human", "confidence": 0.175, "llm_score": 0.09, "burstiness_score": 0.6393, "status": "classified", "appeal": null}
{"content_id": "4c075a5b-...", "creator_id": "appeal-test-user", "timestamp": "2026-06-28T20:36:07+00:00", "attribution": "likely_ai", "confidence": 0.9205, "llm_score": 0.95, "burstiness_score": 0.0, "status": "under_review", "appeal": {"appeal_id": "1a5eff2c-...", "appeal_timestamp": "2026-06-28T20:39:07+00:00", "creator_reasoning": "I wrote this myself. I am a non-native English speaker and my writing style may appear more formal than typical.", "contact_email": null}}
```

The second entry shows an appealed submission: `status` is `under_review`, the original `confidence` and `attribution` are preserved unchanged, and the creator's reasoning appears in the `appeal` sub-object alongside the original decision.

---

## Project Structure

```
├── app.py              # Flask app — routes, rate limiting, pipeline orchestration
├── aggregator.py       # Confidence scoring: weighted average + disagreement penalty + caps
├── audit.py            # Append-only JSON Lines audit log
├── labels.py           # Transparency label constants and selection logic
├── signals/
│   ├── perplexity.py   # Signal 1: LLM-based authorship classification (Groq)
│   └── burstiness.py   # Signal 2: Stylometric variance heuristics
├── planning.md         # Architecture narrative, signal design, edge cases
├── test_labels.py      # Verify all label variants and thresholds (no API needed)
├── test_signal1.py     # Smoke test for Signal 1 (requires GROQ_API_KEY)
├── test_signal2.py     # Smoke test for Signal 2 + side-by-side comparison
└── requirements.txt
```
