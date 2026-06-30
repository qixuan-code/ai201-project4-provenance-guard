import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from aggregator import aggregate_confidence
from audit import log_decision, get_entries, update_entry, get_entry
from labels import get_label, get_attribution
from signals.burstiness import compute_burstiness_score
from signals.perplexity import compute_perplexity_score

app = Flask(__name__)

# Rate limits per planning.md §Rate Limiting:
#   10 req/min  — prevents real-time threshold probing
#   100 req/hr  — allows batch use without sustained automated abuse
#   500 req/day — daily cap against overnight scraping campaigns
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute")
def submit():
    # ── 1. Parse and validate input ──────────────────────────────────────────
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    missing = [f for f in ("text", "creator_id") if f not in data]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    text = data["text"]
    creator_id = data["creator_id"]

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text must be a non-empty string"}), 400
    if len(text) > 50_000:
        return jsonify({"error": "text exceeds maximum length of 50,000 characters"}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "creator_id must be a non-empty string"}), 400

    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    requester_id = request.remote_addr or "unknown"
    word_count = len(text.split())

    # ── 2. Signal 1: LLM classification (Groq) ──────────────────────────────
    # Returns (signal1_raw, signal1_score):
    #   signal1_raw   ∈ [0,1]: AI probability from LLM
    #   signal1_score ∈ [0,1]: 0=AI-like, 1=human-like
    #   ai_s1 = signal1_raw (= 1 - signal1_score)
    try:
        s1_raw, s1_score = compute_perplexity_score(text)
    except Exception as exc:
        app.logger.error("Signal 1 failed: %s", exc)
        return jsonify({"error": "Detection pipeline unavailable", "detail": str(exc)}), 503

    ai_s1 = s1_raw if s1_raw != 0.0 else 0.5   # 0.0 == sentinel for unavailable

    # ── 3. Signal 2: Burstiness heuristic (local, no API) ───────────────────
    # Returns (burstiness_raw, burstiness_score):
    #   burstiness_raw   = B coefficient ∈ (-1, 1)
    #   burstiness_score ∈ [0,1]: 0=AI-like, 1=human-like
    #   ai_s2 = 1 - burstiness_score
    s2_raw, s2_score = compute_burstiness_score(text)
    ai_s2 = 1.0 - s2_score   # sentinel (0.0, 0.5) → ai_s2 = 0.5 (neutral)

    # ── 4. Confidence aggregation (planning.md §1) ───────────────────────────
    agg = aggregate_confidence(ai_s1, ai_s2, word_count)
    confidence = agg["confidence"]

    signals = {
        "perplexity_raw": s1_raw,
        "perplexity_score": s1_score,
        "burstiness_raw": s2_raw,
        "burstiness_score": s2_score,
        "ai_s1": ai_s1,
        "ai_s2": ai_s2,
        "disagreement": agg["disagreement"],
        "overlap_cap_applied": agg["overlap_cap_applied"],
        "short_text": agg["short_text"],
    }

    # ── 5. Attribution and label ──────────────────────────────────────────────
    attribution = get_attribution(confidence)
    label = get_label(confidence)

    # ── 6. Audit log ──────────────────────────────────────────────────────────
    # Written before response is returned so the record exists even if the
    # HTTP connection drops after this point.
    log_decision({
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": round(confidence, 4),
        "llm_score": round(s1_raw, 4),
        "burstiness_score": round(s2_score, 4),
        "status": "classified",
        "appeal": None,   # populated by POST /appeal; null here means no appeal filed
    })

    # ── 7. Response ───────────────────────────────────────────────────────────
    return jsonify({
        "content_id": content_id,
        "timestamp": timestamp,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": round(confidence, 4),
        "signals": signals,
        "label": label,
    }), 200


@app.route("/appeal/<content_id>", methods=["POST"])
def appeal(content_id):
    """
    POST /appeal/<content_id>
    Creator contests a classification. Captures their reasoning, appends an
    appeal record to the audit log, and sets status to "under_review".
    No re-classification occurs (planning.md §4).

    Body:
        creator_statement  str  required, 10–2000 chars
        contact_email      str  optional
    """
    # ── 1. Validate the content_id exists ────────────────────────────────────
    original = get_entry(content_id)
    if original is None:
        return jsonify({"error": f"No submission found with content_id {content_id!r}"}), 404

    if original.get("status") == "under_review":
        return jsonify({"error": "An appeal for this submission is already under review"}), 409

    # ── 2. Parse and validate body ───────────────────────────────────────────
    data = request.get_json(force=True, silent=True) or {}
    # Accept both field names for compatibility
    reasoning = (data.get("creator_reasoning") or data.get("creator_statement") or "").strip()

    if len(reasoning) < 10:
        return jsonify({"error": "creator_reasoning must be at least 10 characters"}), 400
    if len(reasoning) > 2000:
        return jsonify({"error": "creator_reasoning must be 2000 characters or fewer"}), 400

    contact_email = data.get("contact_email")

    # ── 3. Write appeal record + update status ───────────────────────────────
    appeal_id = str(uuid.uuid4())
    appeal_timestamp = datetime.now(timezone.utc).isoformat()

    updated = update_entry(content_id, {
        "status": "under_review",
        "appeal": {
            "appeal_id": appeal_id,
            "appeal_timestamp": appeal_timestamp,
            "creator_reasoning": reasoning,
            "contact_email": contact_email,
        },
    })

    if not updated:
        # Race condition: entry disappeared between get_entry and update_entry
        return jsonify({"error": "Failed to update audit log — please retry"}), 500

    # ── 4. Response ───────────────────────────────────────────────────────────
    return jsonify({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal has been recorded. A reviewer will examine your submission.",
        "appeal_timestamp": appeal_timestamp,
    }), 200


@app.route("/log", methods=["GET"])
def log():
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    entries = get_entries(status=status, limit=limit, offset=offset)
    return jsonify({"entries": entries, "count": len(entries)}), 200


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded", "detail": str(e.description)}), 429


@app.errorhandler(400)
def bad_request_handler(e):
    return jsonify({"error": "Bad request", "detail": str(e.description)}), 400


if __name__ == "__main__":
    app.run(debug=True)
