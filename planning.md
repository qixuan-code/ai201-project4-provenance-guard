# ProvenanceGuard — Planning Document

## Architecture

### Diagram

```
SUBMISSION FLOW
───────────────────────────────────────────────────────────────────────────────

  Client
  POST /submit
  (raw text, requester ID)
       │
       ▼
 ┌─────────────┐  over quota   ┌─────────────────┐
 │ Rate Limiter│ ─────────────▶│  429 Too Many   │
 │ sliding win │               │  Requests       │
 └──────┬──────┘               └─────────────────┘
        │ raw text (passes quota check)
        ├─────────────────────────────────┐
        │                                 │
        ▼                                 ▼
 ┌─────────────────┐             ┌─────────────────┐
 │    Signal 1     │             │    Signal 2     │
 │   Perplexity    │             │   Burstiness    │
 │ (LM token probs)│             │(sentence varian)│
 └────────┬────────┘             └────────┬────────┘
          │ score₁ [0,1]                  │ score₂ [0,1]
          └──────────────┬────────────────┘
                         ▼
                ┌─────────────────┐
                │   Confidence    │
                │   Aggregator    │
                │ weighted avg +  │
                │ disagreement    │
                │ penalty +       │
                │ overlap cap     │
                └────────┬────────┘
                         │ combined score + attribution label
                         │
               ┌─────────┴──────────┐
               │                    │
               ▼                    ▼
      ┌─────────────────┐  ┌─────────────────┐
      │ Label Generator │  │   Audit Log     │
      │ maps score →    │  │ append-only     │
      │ label variant   │  │ content ID,     │
      │ (3 types)       │  │ scores,         │
      └────────┬────────┘  │ timestamp,      │
               │           │ label variant   │
               │ label text└────────┬────────┘
               │                    │ log record ID
               └──────────┬─────────┘
                           ▼
                  ┌─────────────────┐
                  │    Response     │
                  │ attribution +   │
                  │ confidence +    │
                  │ label text      │
                  └─────────────────┘


APPEAL FLOW
───────────────────────────────────────────────────────────────────────────────

  Client
  POST /appeal/:contentId
  (content ID, creator reasoning)
       │
       ▼
 ┌─────────────────┐
 │ Status Updater  │
 │ sets            │
 │ "under_review"  │
 │ stores          │
 │ reasoning       │
 └────────┬────────┘
          │ appeal record         ┌─────────────────┐
          │ appended to log ─────▶│   Audit Log     │
          │                       │ (shared with    │
          │                       │  submission     │
          │                       │  flow above)    │
          │                       └─────────────────┘
          │ updated status
          ▼
 ┌─────────────────┐
 │    Response     │
 │ appeal ID +     │
 │ "under_review"  │
 │ status          │
 └─────────────────┘
```

### Narrative

In the **submission flow**, every request passes through the Rate Limiter before anything else; over-quota requests are rejected at the door and never reach the detection pipeline. The two signals (Perplexity and Burstiness) run independently on the raw text and each return a score in [0, 1]; the Confidence Aggregator combines them with a weighted average, applies a disagreement penalty when signals contradict each other, and caps confidence in the high-overlap region where polished human writing is indistinguishable from AI output. The Label Generator and Audit Log both receive the aggregated result — the log is written before the response is returned, so every decision is recorded even if the HTTP connection drops.

In the **appeal flow**, the only component that runs is the Status Updater, which appends the creator's statement to the existing audit log entry and flips its status to `"under_review"` without touching the original signal scores or confidence. The Audit Log is the single shared store for both flows: a human reviewer opening the appeal queue sees the original detection record and the creator's statement together in one entry, with no information lost or overwritten.

---

## Architecture Narrative (extended)

---

## 1. Detection Signals

### Signal 1 — Perplexity (LM token probability)

**What it measures.** Each token in the text is scored by how probable it is given its context, using a small pretrained language model (`distilgpt2`). The mean of those log-probabilities yields a perplexity score. Low perplexity means the text was easy to predict — almost every word choice was expected.

**Why it differs between human and AI writing.** LLMs are trained to maximize the probability of the next token, so they inherently produce text that other language models also find unsurprising: locally optimal word choices, smooth transitions, conventional sentence completions. Human writers make idiosyncratic choices — unusual metaphors, syntax interruptions, domain jargon used out of register — that register as higher perplexity.

**Output format.** Raw perplexity is unbounded (typically 20–200 for natural text). It will be min-max normalized against calibration data to produce `perplexity_score ∈ [0, 1]`, where **0 = low perplexity (AI-like), 1 = high perplexity (human-like)**. The signal therefore contributes to the AI confidence score as `1 - perplexity_score`.

**Blind spots.** Formulaic human writing (legal boilerplate, academic abstracts, news wire copy) scores as low-perplexity. AI output sampled at high temperature or deliberately randomized scores as high-perplexity. Short texts (< ~150 tokens) produce unstable estimates. AI fine-tuned on a specific human's writing inherits their perplexity profile.

---

### Signal 2 — Burstiness / Stylometric Variance

**What it measures.** Human writing is "bursty" — sentence lengths cluster in runs, then suddenly shift. This signal computes (a) the variance of sentence lengths, (b) the burstiness coefficient `B = (σ - μ) / (σ + μ)` where σ and μ are the standard deviation and mean of inter-sentence-length intervals, and (c) punctuation rhythm (ratio of commas and em-dashes to total sentences as a proxy for syntactic complexity variation). High burstiness → irregular, human-like rhythm. Low burstiness → suspiciously uniform.

**Why it differs between human and AI writing.** LLMs sample from distributions that average across training data, producing sentence-length distributions that are more uniform than human writing — fewer one-word punches, fewer sprawling compound sentences, more medium-length uniformity. Human writing, especially personal or literary work, uses short sentences for emphasis and long ones for elaboration, and these contrasts cluster intentionally.

**Output format.** The burstiness coefficient B ∈ (-1, 1) is rescaled to `burstiness_score ∈ [0, 1]`, where **0 = uniform (AI-like), 1 = bursty (human-like)**. Contributes to AI confidence as `1 - burstiness_score`.

**Blind spots.** Heavily edited or polished human prose (published journalism, copy-edited essays) is also more uniform. Poetry intended as a uniform chant (e.g., villanelles, anaphoric verse) will score as AI-like. Any text under ~8 sentences lacks enough data points for meaningful variance. A sophisticated adversary can manually introduce short sentences to inflate the score.

---

### Combining Signals into a Single Confidence Score

Each signal contributes an "AI likelihood" sub-score: `ai_s1 = 1 - perplexity_score` and `ai_s2 = 1 - burstiness_score`.

**Base score** (weighted average, weights tunable):
```
base_confidence = 0.6 * ai_s1 + 0.4 * ai_s2
```

Signal 1 (perplexity) gets higher weight because it is more directly grounded in LM behavior; Signal 2 is a useful corroborating signal but noisier on short texts.

**Disagreement penalty.** When the two sub-scores diverge significantly, the aggregator pulls the final score toward 0.5:
```
disagreement = |ai_s1 - ai_s2|
penalized_confidence = base_confidence * (1 - 0.3 * disagreement)
```

A disagreement of 1.0 (signals pointing opposite directions) caps the maximum confidence at 0.7, preventing the system from expressing high certainty when its own signals contradict each other.

**Overlap region cap.** Calibration data will identify the region of signal space where human and AI distributions overlap heavily (roughly: low perplexity AND low burstiness, which describes polished human writing). In this region, `penalized_confidence` is capped at 0.72 regardless of sub-score values, reflecting irreducible uncertainty.

**Final output:**
```json
{
  "attribution": "ai" | "human" | "uncertain",
  "confidence": 0.0–1.0,
  "signals": {
    "perplexity_score": 0.0–1.0,
    "burstiness_score": 0.0–1.0,
    "ai_s1": 0.0–1.0,
    "ai_s2": 0.0–1.0,
    "disagreement": 0.0–1.0
  }
}
```

---

## 2. Uncertainty Representation

### What the confidence score means

`confidence` is the system's estimated probability that the content was AI-generated, after applying the disagreement penalty and overlap cap. It is not a raw model probability — it is a calibrated score designed to be meaningful to downstream consumers.

A score of **0.60** means: the signals lean toward AI, but not strongly. One signal may be uncertain, or both may be only weakly pointing the same direction. The system cannot distinguish this text from human writing with confidence. This should produce a label that communicates ambiguity, not a finding.

A score of **0.95** means: both signals strongly agree, the text sits in the low-overlap region of signal space, and the disagreement penalty did not trigger. The system has high confidence this is AI-generated.

### Thresholds

| Confidence | Attribution label | Rationale |
|---|---|---|
| ≥ 0.80 | `"ai"` | Both signals agree strongly; false positive rate acceptable |
| 0.40 – 0.79 | `"uncertain"` | Signals weak, disagreeing, or text in overlap region |
| ≤ 0.39 | `"human"` | Both signals strongly suggest human authorship |

The "uncertain" band is intentionally wide (40 points). Asymmetric cost: a false positive (flagging a human as AI) is meaningfully worse than an uncertain label. The 0.80 threshold is higher than a naive 0.5 midpoint to reflect this.

### Calibration approach

Before deployment, 200+ labeled samples (known-human and known-AI) will be scored by the raw pipeline. The min-max normalization parameters for `perplexity_score` and the burstiness rescaling will be fit to this calibration set. Reliability diagrams (predicted probability vs. actual frequency) will verify that a score of 0.65 actually corresponds to ~65% of those samples being AI. If not, Platt scaling or isotonic regression will be applied.

---

## 3. Transparency Label Design

The label is a structured object:

```typescript
type TransparencyLabel = {
  variant: "ai" | "human" | "uncertain";
  headline: string;
  body: string;
  confidence_display: string;  // human-readable direction + certainty, not a raw number
  appeal_prompt: string | null; // null for high-confidence human (no appeal warranted)
}
```

### Variant 1 — High-confidence AI (`confidence ≥ 0.80`)

```
headline: "Likely AI-generated"
body: "Our system found patterns consistent with AI-generated text — specifically,
       predictable word choices and unusually uniform sentence rhythm. These are
       statistical tendencies, not proof of authorship."
confidence_display: "High confidence"
appeal_prompt: "If you wrote this yourself, share the context below and we'll review it."
```

**Changes from draft:** Removed the phrase "patterns appear in the majority of AI-generated content we've analyzed" — that implies a large validated corpus we don't have. Changed "contest this finding" → "share the context" to frame the appeal as providing information rather than fighting a verdict.

---

### Variant 2 — High-confidence Human (`confidence ≤ 0.39`)

```
headline: "Likely written by a person"
body: "Our system found patterns consistent with human authorship — varied word choices
       and irregular sentence rhythm that differ from what we typically see in
       AI-generated text."
confidence_display: "High confidence"
appeal_prompt: null
```

**Changes from draft:** Removed the appeal prompt entirely. A creator labeled "human" has no reason to appeal. Surfacing an appeal option on a positive result creates confusion and implies doubt where the system has none. If a platform operator wants to report a false negative, that's a separate admin channel, not a creator-facing label.

---

### Variant 3a — Uncertain, leaning AI (`confidence 0.65–0.79`)

```
headline: "Origin unclear — some AI patterns detected"
body: "Our system found some patterns associated with AI-generated text, but the
       signal is mixed and not strong enough for a firm conclusion. This is a
       preliminary flag, not a finding. Polished or formally written prose, short
       texts, and certain genres often produce this result."
confidence_display: "Uncertain — leaning AI"
appeal_prompt: "If you wrote this yourself, share the context and we'll take another look."
```

---

### Variant 3b — Uncertain, no clear signal (`confidence 0.40–0.64`)

```
headline: "Origin unclear"
body: "Our system found no strong signal in either direction. This text did not
       show clear patterns of either human or AI authorship. This is not a flag —
       it means our tools don't have enough information to make a useful call."
confidence_display: "Uncertain — no clear signal"
appeal_prompt: "Something seem wrong? You can share context below."
```

**Why split the uncertain band:** A 0.75 and a 0.43 are genuinely different situations — the first leans AI but doesn't clear the threshold; the second is truly ambiguous. Showing the same label for both erases a meaningful distinction. Variant 3a is the false-positive risk zone and its label copy reflects that; Variant 3b is genuine ignorance and its copy says so plainly.

---

**Invariants across all variants.** No variant uses the words "cheating," "plagiarism," "dishonest," or "fraud." Every AI-leaning variant frames the result as a statistical pattern, not a judgment about intent. The appeal prompt, where present, frames the creator as providing context, not mounting a defense.

---

## 4. Appeals Workflow

### Who can submit an appeal

Any creator who holds the `content_id` for a submission. In practice, the submitter receives the `content_id` in the original `POST /submit` response and uses it to construct an appeal. No authentication beyond possession of the `content_id` is required in the MVP. (Future: tie submissions to authenticated accounts.)

### What information the appeal captures

```typescript
type AppealRequest = {
  content_id: string;      // links appeal to original decision
  creator_statement: string;  // required, min 10 chars, max 2000 chars
  contact_email?: string;  // optional, for follow-up
}
```

The `creator_statement` is the only required substantive field. The system does not ask the creator to "prove" authorship — that would be impossible and accusatory. The statement is their opportunity to explain context: "I write in a formal academic style," "This is a translated excerpt," "I used an AI tool for grammar checking but wrote all the content."

### What the system does when an appeal is received

1. Validate that the `content_id` exists in the audit log.
2. Write an appeal record to the audit log, appended to the original entry:
   ```json
   {
     "type": "appeal",
     "appeal_id": "uuid",
     "content_id": "...",
     "timestamp": "ISO8601",
     "creator_statement": "...",
     "contact_email": "..." | null
   }
   ```
3. Update the content's status field in the audit log from `"decided"` to `"under_review"`.
4. Return a response with the `appeal_id` and a confirmation message.

No re-classification occurs. The original confidence score and label are preserved in the log — they are not overwritten.

### What a human reviewer sees in the appeal queue

`GET /log?status=under_review` returns all entries with status `"under_review"`, each containing:

- Original submission: `content_id`, `timestamp`, `attribution`, `confidence`, `signals` (both raw scores), `label_variant`, content text (or a hash + retrieval key if content is not stored inline)
- Appeal record: `appeal_id`, `creator_statement`, `contact_email`, `appeal_timestamp`

The reviewer sees the full picture in one record: what the system decided, why (signal scores), how confident it was, and what the creator said. They can then manually update the status to `"overturned"` or `"upheld"` via a `PATCH /log/:content_id` endpoint.

---

## 5. Anticipated Edge Cases

### Edge case 1: A villanelle or highly repetitive poem

A villanelle repeats two refrains throughout its 19 lines. The repeated lines will produce extremely low perplexity (the model has seen those exact words and predicts them with high confidence after the first occurrence). The burstiness signal will also be low because the line lengths are intentionally uniform by the form's rules. Both signals point toward AI, but the content is one of the most strictly human-defined literary forms in the English language.

**How the system responds.** If the text is short (19 lines, < 150 tokens), the perplexity estimate is unstable and the system should down-weight Signal 1. Even so, both signals may fire. The expected output is a `confidence` around 0.72–0.78 — below the 0.80 threshold due to the disagreement penalty or overlap cap, landing in the "uncertain" band. The label reads "Origin unclear" with an invitation to appeal. The creator's statement ("This is a villanelle, a fixed-form poem") gives the human reviewer immediate context to overturn it.

**Mitigation to implement.** A content-length guard: if the text is under 150 tokens, add a fixed 0.12 penalty to `disagreement` before computing `penalized_confidence`. This forces short-text results into the uncertain band more aggressively.

---

### Edge case 2: A non-native English speaker writing carefully in their second language

A careful L2 writer often self-edits toward "correct" and away from idiomatic risk. They choose common words (low perplexity), write shorter, more uniform sentences to avoid grammatical mistakes (low burstiness), and avoid the stylistic detours that mark native-speaker fluency. The result looks statistically similar to AI output on both signals.

**How the system responds.** Likely `confidence` of 0.78–0.85, potentially crossing the 0.80 threshold into the high-confidence AI label. This is the false positive scenario most likely to cause real harm — the creator is human, their writing looks "AI" to the heuristics, and they may not have the vocabulary in their second language to write an effective appeal.

**Mitigation to implement.** The overlap cap (described in Section 1) must cover this region. Low perplexity combined with low burstiness is exactly where L2 and formal-register human writing lives. If `ai_s1 < 0.35` AND `ai_s2 < 0.35` (both signals in the "uniformly simple" zone), cap `penalized_confidence` at 0.72 regardless of weights, keeping the result in the uncertain band. Document this cap explicitly in the README so reviewers know the system is designed to be conservative here.

---

### Edge case 3: AI content that was lightly human-edited

A user generates a paragraph with GPT-4, then edits 10–15% of the words to sound more personal — swaps a few vocabulary choices, breaks one long sentence into two short ones, adds a rhetorical question. The perplexity may increase slightly (new idiosyncratic word choices), and burstiness increases (the broken sentence creates a length contrast). The two signals may disagree: Signal 1 still reads AI-like, Signal 2 now reads more human-like.

**How the system responds.** High disagreement → disagreement penalty fires → `penalized_confidence` is pulled toward 0.5 → likely lands in the uncertain band around 0.55–0.65. The label reads "Origin unclear." This is actually the correct behavior — the content is partially human. The system should not confidently assert either label for hybrid content.

**Limitation to document.** The system cannot detect the *degree* of human modification or identify which parts are AI-generated vs. human-written. It produces a single document-level signal. Mixed-origin content is a genuinely hard problem that no single-document signal approach can solve reliably.

---

## Rate Limiting Design

| Limit | Value | Reasoning |
|---|---|---|
| Requests per IP per minute | 10 | Prevents real-time probing: an attacker trying to find the threshold between "uncertain" and "ai" would need many rapid submissions |
| Requests per IP per hour | 100 | Allows legitimate batch use (a platform uploading a backlog of content) without enabling sustained automated abuse |
| Requests per IP per day | 500 | Daily cap prevents overnight scraping campaigns |
| Appeal submissions per content_id | 1 | Prevents appeal-flooding a single entry; one statement per creator |

Window type: sliding window (not fixed bucket) to prevent burst abuse at window boundaries.

---

## Audit Log Schema

```typescript
type AuditEntry = {
  content_id: string;          // uuid, returned to submitter
  timestamp: string;           // ISO 8601
  requester_id: string;        // IP or API key hash
  status: "decided" | "under_review" | "overturned" | "upheld";
  
  // Detection results
  attribution: "ai" | "human" | "uncertain";
  confidence: number;          // 0.0–1.0
  label_variant: "ai" | "human" | "uncertain";
  
  // Raw signals
  signals: {
    perplexity_raw: number;    // unnormalized perplexity
    perplexity_score: number;  // normalized [0,1]
    burstiness_raw: number;    // B coefficient
    burstiness_score: number;  // rescaled [0,1]
    ai_s1: number;
    ai_s2: number;
    disagreement: number;
  };
  
  // Optional appeal record (appended, not replacing)
  appeal?: {
    appeal_id: string;
    appeal_timestamp: string;
    creator_statement: string;
    contact_email: string | null;
  };
  
  // Optional reviewer decision
  review?: {
    reviewed_by: string;
    review_timestamp: string;
    decision: "overturned" | "upheld";
    reviewer_notes: string;
  };
}
```

---

## Architecture Diagram Reference

See the architecture diagram (generated separately) for a visual representation of the two main flows: submission (`POST /submit` → Rate Limiter → Signal 1 + Signal 2 → Confidence Aggregator → Label Generator → Audit Log → Response) and appeal (`POST /appeal/:id` → Status Updater → Audit Log → Response).
