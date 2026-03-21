#!/usr/bin/env python3
"""
Call Analysis Engine — Layer 2 + 3 of the Conversation Intelligence Platform.

Per call (Claude LLM):
  - Call summary + key moments
  - Customer intent + sentiment trajectory
  - Conversation flow (opening → needs discovery → pitch → objections → close)
  - Agent performance scores across 6 dimensions
  - Objection handling quality
  - Outcome classification (converted / follow-up / lost)
  - Coaching cues + winning/losing moments

Aggregated (across all calls):
  - Agent scorecards with conversion rates
  - Top objections and pain points
  - Winning and losing patterns
  - Sales improvement recommendations
  - Coaching themes for training

Usage:
  python analyze_calls.py                  # analyze all new transcripts
  python analyze_calls.py --limit 10       # analyze first 10 only (test run)
  python analyze_calls.py --reanalyze      # re-analyze even if already done
  python analyze_calls.py --report-only    # skip per-call, just regenerate report
"""

import os, sys, json, re, glob, argparse, time
from json_repair import repair_json
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from system_prompt_bd import get_claude_cached_system as get_bd_system, PROMPT_VERSION as BD_PROMPT_VERSION
from system_prompt_cs import get_claude_cached_system as get_cs_system, PROMPT_VERSION as CS_PROMPT_VERSION
from agents import resolve_agent, resolve_agent_by_fullname

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = "/home/user/Documents/AI/SalesScorecard"
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")
ANALYSIS_DIR    = os.path.join(BASE_DIR, "analysis")
KEY_FILE        = os.path.join(BASE_DIR, "anthropic_key.txt")
# OPENAI_KEY_FILE = os.path.join(BASE_DIR, "OpenAI_Woodenstreet_key")  # OpenAI — commented out
MODEL             = "claude-3-haiku-20240307"
MIN_WORDS         = 150   # skip near-empty calls (< 150 words = noise/wrong number/dropped)
BATCH_STATE_FILE  = os.path.join(BASE_DIR, "batch_state.json")
# ─────────────────────────────────────────────────────────────────────────────


# ── API key ───────────────────────────────────────────────────────────────────
def load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    # key = os.environ.get("OPENAI_API_KEY", "").strip()  # OpenAI — commented out
    if key:
        return key
    if os.path.isfile(KEY_FILE):
        key = open(KEY_FILE).read().strip()
        if key:
            return key
    print("ERROR: No Anthropic API key found.")
    print("  Option 1: export ANTHROPIC_API_KEY=your_key")
    print(f"  Option 2: save your key to: {KEY_FILE}")
    sys.exit(1)


# ── Filename metadata parser ──────────────────────────────────────────────────
def parse_filename(filepath: str) -> dict:
    """Extract timestamp, agent full name, team, location, phone from filename.

    Handles two formats:
      OLD (pre-Ozonetel archive): YYYYMMDD-HHMMSS_phone_BD Sales - Name LOC-all_diarized.json
      NEW (Ozonetel archive):     DDMMYYYY__Agent_Name__HHMMSS__CallID_diarized.json
    """
    stem = Path(filepath).stem.replace("_diarized", "")

    # ── NEW Ozonetel format: DDMMYYYY__Agent_Name__HHMMSS__CallID ──
    new_match = re.match(r"^(\d{8})__(.+?)__(\d{6})__(\d+)$", stem)
    if new_match:
        d, agent_raw, t, _ = new_match.groups()
        # Date is DDMMYYYY
        timestamp = f"{d[4:8]}-{d[2:4]}-{d[0:2]} {t[:2]}:{t[2:4]}:{t[4:]}"
        agent_info = resolve_agent_by_fullname(agent_raw)
        return {
            "timestamp":      timestamp,
            "agent_name":     agent_info["full_name"],
            "agent_short":    agent_raw.replace("_", " "),
            "location":       agent_info["location"],
            "team":           agent_info.get("team", "Unknown"),
            "agent_status":   agent_info.get("status", "Unknown"),
            "customer_phone": None,
        }

    # ── OLD format: YYYYMMDD-HHMMSS_phone_BD Sales - Name LOC-all ──
    ts_match = re.match(r"(\d{8})-(\d{6})", stem)
    timestamp = None
    if ts_match:
        d, t = ts_match.group(1), ts_match.group(2)
        timestamp = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"

    phone_match = re.search(r"_(\d{8,15})_", stem)
    phone = phone_match.group(1) if phone_match else None

    agent_match = re.search(r"BD Sales - (.+?)-all", stem)
    short_name = agent_match.group(1).strip() if agent_match else None

    agent_info = resolve_agent(short_name)

    return {
        "timestamp":      timestamp,
        "agent_name":     agent_info["full_name"],
        "agent_short":    short_name,
        "location":       agent_info["location"],
        "team":           agent_info.get("team", "BD Sales"),
        "agent_status":   agent_info.get("status", "Unknown"),
        "customer_phone": phone,
    }


# ── Transcript formatter ──────────────────────────────────────────────────────
def format_transcript(turns: list) -> str:
    """Format speaker turns as readable AGENT / CUSTOMER dialogue."""
    lines = []
    for t in turns:
        role = "AGENT" if t["speaker"] == "SPEAKER_00" else "CUSTOMER"
        lines.append(f"{role}: {t['text']}")
    return "\n".join(lines)


# ── Aggregate synthesis prompt ────────────────────────────────────────────────
AGGREGATE_PROMPT = """\
You are analyzing a batch of WoodenStreet sales call analyses to produce team-level insights.

Below is a JSON summary of all {n} analyzed calls including agent scores, outcomes,
objections, pain points, winning moments, losing moments, and customer voice data
(what customers asked for, issues they raised, and gaps they experienced).

Data:
{data}

Return ONLY valid JSON with this structure (no markdown):
{{
  "executive_summary": "3-4 sentence overview of the team's performance",

  "top_10_customer_asks": ["top ask 1", "top ask 2", "top ask 3", "top ask 4", "top ask 5"],
  "top_10_issues": ["top issue 1", "top issue 2", "top issue 3", "top issue 4", "top issue 5"],
  "top_10_gaps": ["top gap 1", "top gap 2", "top gap 3", "top gap 4", "top gap 5"],

  "top_winning_patterns": [
    {{"pattern": "description", "why_it_works": "short explanation", "example": "brief example"}}
  ],
  "top_losing_patterns": [
    {{"pattern": "description", "impact": "what it costs", "example": "brief example"}}
  ],
  "top_objections": [
    {{"objection": "description", "frequency": "high/medium/low", "best_response": "short response"}}
  ],
  "revenue_leakage_signals": ["signal 1", "signal 2", "signal 3"],

  "what_to_teach_agents": [
    {{"skill": "skill name", "why": "reason", "how": "training approach"}}
  ],
  "sales_improvement_focus": [
    {{"area": "focus area", "current_state": "what's happening", "target": "what good looks like"}}
  ],
  "top_agents_by_score": ["agent names ranked by overall score"],
  "agents_needing_coaching": [
    {{"agent": "name", "weakness": "main gap", "priority": "high/medium"}}
  ]
}}"""


# ── Schema normalizer (handles old and new analysis schemas) ──────────────────
def normalize_analysis(analysis: dict) -> dict:
    """
    Bridge between:
      - Old schema  : agent_scores, outcome, winning_moments, etc.
      - New schema  : agent_scorecard (WoodenStreet BD Sales specific field names)
    Returns a consistent dict for aggregate aggregation.
    """
    # ── New schema (WoodenStreet system_prompt.py — field names already match) ─
    if "agent_scorecard" in analysis:
        sc  = analysis.get("agent_scorecard", {})
        int_= analysis.get("intent", {})
        sen = analysis.get("sentiment", {})
        out = analysis.get("call_outcome", {})
        cv  = analysis.get("customer_voice", {})

        # Map resolution_type → outcome bucket
        res = out.get("resolution_type", "unclear")
        outcome_map = {
            "sale_converted":          "converted",
            "store_visit_booked":      "follow_up_scheduled",
            "follow_up_scheduled":     "follow_up_scheduled",
            "complaint_resolved":      "support_resolved",
            "partially_resolved":      "follow_up_scheduled",
            "transferred":             "follow_up_scheduled",
            "unresolved":              "lost",
        }
        outcome = outcome_map.get(res, "unclear")

        coaching_tip = sc.get("coaching_tip", "")
        missed_opp   = sc.get("missed_opportunity", "")

        return {
            "agent_scores": {
                # Field names now match new schema directly — no remapping needed
                "opening_greeting":    sc.get("opening_greeting", 0),
                "needs_discovery":     sc.get("needs_discovery", 0),
                "product_knowledge":   sc.get("product_knowledge", 0),
                "objection_handling":  sc.get("objection_handling", 0),
                "closing_attempt":     sc.get("closing_attempt", 0),
                "empathy_tone":        sc.get("empathy_tone", 0),
            },
            "outcome":            outcome,
            "customer_intent":    int_.get("primary_intent", "other"),
            "customer_sentiment": sen.get("overall", "neutral"),
            "winning_moments":    sc.get("strengths", []),
            "losing_moments":     sc.get("improvement_areas", []),
            "missed_opportunities": [missed_opp] if missed_opp else [],
            "objections_raised":    [
                f.get("description", "")
                for f in analysis.get("red_flags", [])
                if f.get("type") == "churn_risk"
            ],
            "customer_pain_points": sen.get("churn_signals", []),
            "coaching_cues":        [coaching_tip] if coaching_tip else [],
            "feature_requests":     [],
            # Customer voice — new fields
            "top_asks":              cv.get("top_asks", []),
            "issues_raised":         cv.get("issues_raised", []),
            "product_service_gaps":  cv.get("product_service_gaps", []),
            "unmet_needs":           cv.get("unmet_needs", []),
            "positive_feedback":     cv.get("positive_feedback", []),
        }

    # ── Old schema (PER_CALL_PROMPT based) — return as-is ───────────────────
    return analysis


# ── Claude API call ───────────────────────────────────────────────────────────
def call_claude(prompt: str, client, max_tokens: int = 4000,
                system_content=None) -> dict:
    """Call Claude and parse JSON response. Retries up to 3 times on transient errors."""
    kwargs = dict(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system_content is not None:
        kwargs["system"] = system_content

    last_err = None
    for attempt in range(1, 4):
        try:
            message = client.messages.create(**kwargs)
            raw = message.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                repaired = repair_json(raw, return_objects=True)
                if isinstance(repaired, dict) and repaired:
                    return repaired
                print("  WARNING: Could not parse JSON response, returning empty dict")
                return {}
        except Exception as e:
            last_err = e
            wait = 2 ** attempt          # 2s, 4s, 8s
            print(f"  API error (attempt {attempt}/3): {e} — retrying in {wait}s", flush=True)
            time.sleep(wait)

    print(f"  FAILED after 3 attempts: {last_err}")
    return {}


# ── Shared helpers ─────────────────────────────────────────────────────────────

def get_call_context(json_path: str):
    """Load transcript file and return everything needed to build an API request."""
    with open(json_path) as f:
        data = json.load(f)
    meta           = data["metadata"]
    turns          = data["turns"]
    fname_meta     = parse_filename(json_path)
    team           = fname_meta.get("team", "BD Sales")
    if team == "Customer Support":
        system_content = get_cs_system()
        prompt_version = CS_PROMPT_VERSION
    else:
        system_content = get_bd_system()
        prompt_version = BD_PROMPT_VERSION
    transcript_text = format_transcript(turns)
    return meta, fname_meta, transcript_text, system_content, prompt_version


def build_result_dict(json_path, fname_meta, meta, analysis, prompt_version):
    """Build the standard output dict — identical structure for real-time and batch."""
    return {
        "file":          os.path.basename(json_path),
        "call_metadata": {
            **fname_meta,
            "duration_sec": meta.get("duration_sec"),
            "total_words":  meta["total_words"],
            "num_turns":    meta["num_turns"],
            "language":     meta.get("language"),
            "processed_at": meta.get("processed_at"),
        },
        "analysis":       analysis,
        "prompt_version": prompt_version,
        "analyzed_at":    datetime.now().isoformat(timespec="seconds"),
    }


# ── Real-time per-call processing ─────────────────────────────────────────────
def analyze_one(json_path: str, client) -> dict | None:
    meta, fname_meta, transcript_text, system_content, prompt_version = \
        get_call_context(json_path)

    analysis = call_claude(
        f"Analyze this call transcript:\n\n{transcript_text}",
        client,
        max_tokens=2500,
        system_content=system_content,
    )
    return build_result_dict(json_path, fname_meta, meta, analysis, prompt_version)


# ── Batch API ─────────────────────────────────────────────────────────────────

def build_batch_requests(to_process):
    """Prepare batch request list + id→paths mapping. Skips unreadable files."""
    requests = []
    id_map   = {}  # custom_id → (jf, out_path, day_dir)
    skipped  = 0

    for idx, (jf, out_path, day_dir) in enumerate(to_process):
        custom_id = f"c{idx:05d}"
        try:
            _, _, transcript_text, system_content, _ = get_call_context(jf)
        except Exception as e:
            print(f"  SKIP {Path(jf).name}: {e}")
            skipped += 1
            continue

        requests.append({
            "custom_id": custom_id,
            "params": {
                "model":    MODEL,
                "max_tokens": 2500,
                "system":   system_content,
                "messages": [{"role": "user",
                              "content": f"Analyze this call transcript:\n\n{transcript_text}"}],
            }
        })
        id_map[custom_id] = (jf, out_path, day_dir)

    if skipped:
        print(f"  Skipped {skipped} unreadable files")
    return requests, id_map


def save_batch_state(batch_id, id_map):
    state = {
        "batch_id":     batch_id,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "total":        len(id_map),
        "id_map":       {k: list(v) for k, v in id_map.items()},
    }
    with open(BATCH_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"  Batch state saved → {BATCH_STATE_FILE}")


def load_batch_state():
    if not os.path.exists(BATCH_STATE_FILE):
        return None
    with open(BATCH_STATE_FILE) as f:
        state = json.load(f)
    state["id_map"] = {k: tuple(v) for k, v in state["id_map"].items()}
    return state


def parse_api_response(raw_text):
    """Parse and repair JSON from Claude response — same logic as call_claude()."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw_text.strip())
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, dict) and repaired:
            return repaired
        return {}


def process_batch_results(batch_id, id_map, client):
    """Download batch results and save analysis JSONs. Returns (saved, errors)."""
    saved = 0
    errors = 0
    total = len(id_map)

    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        if custom_id not in id_map:
            continue

        jf, out_path, day_dir = id_map[custom_id]

        if result.result.type == "errored":
            print(f"  ✗ [{saved+errors+1}/{total}] API error on {Path(jf).name}: "
                  f"{result.result.error}", flush=True)
            errors += 1
            continue

        try:
            raw_text = result.result.message.content[0].text
            analysis = parse_api_response(raw_text)
            meta, fname_meta, _, _, prompt_version = get_call_context(jf)
            result_dict = build_result_dict(jf, fname_meta, meta, analysis, prompt_version)
            os.makedirs(day_dir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result_dict, f, indent=2, ensure_ascii=False)
            saved += 1
            print(f"  ✓ [{saved}/{total}] {Path(day_dir).name}/{Path(out_path).name}",
                  flush=True)
        except Exception as e:
            print(f"  ✗ Error saving {custom_id} ({Path(jf).name}): {e}", flush=True)
            errors += 1

    return saved, errors


def poll_and_save(batch_id, id_map, client):
    """Poll until batch ends, then save all results."""
    print(f"\n  Polling batch {batch_id} (every 30s) ...")
    while True:
        batch  = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(f"  [{datetime.now().strftime('%H:%M:%S')}]  "
              f"processing={counts.processing}  succeeded={counts.succeeded}  "
              f"errored={counts.errored}", flush=True)
        if batch.processing_status == "ended":
            break
        time.sleep(30)

    print("\n  Batch complete — saving results ...")
    saved, errors = process_batch_results(batch_id, id_map, client)

    # Clean up state file once done
    if os.path.exists(BATCH_STATE_FILE):
        os.remove(BATCH_STATE_FILE)

    print(f"\n  Saved: {saved}   Errors: {errors}")
    if errors:
        print("  Re-run with --batch to retry failed calls (already-done files skipped)")
    return saved


def run_batch_mode(to_process, client):
    """Build requests, submit batch, poll, save results."""
    total = len(to_process)
    print(f"\nBuilding {total} batch requests ...")
    requests, id_map = build_batch_requests(to_process)

    if not requests:
        print("  Nothing to submit.")
        return 0

    print(f"Submitting batch ({len(requests)} requests to Anthropic) ...")
    batch = client.messages.batches.create(requests=requests)
    print(f"  Batch ID : {batch.id}")
    print(f"  Status   : {batch.processing_status}")
    save_batch_state(batch.id, id_map)

    return poll_and_save(batch.id, id_map, client)


# ── Aggregate report ──────────────────────────────────────────────────────────
def build_aggregate_report(all_results: list, client) -> dict:
    """Compute scorecards + call Claude for pattern synthesis."""
    valid = [r for r in all_results if r and "analysis" in r]
    print(f"\n  Building aggregate from {len(valid)} analyzed calls ...", flush=True)

    # ── Scorecards ──
    agent_scores   = defaultdict(lambda: defaultdict(list))
    agent_outcomes = defaultdict(list)
    agent_calls    = defaultdict(int)

    for r in valid:
        agent = r["call_metadata"].get("agent_name") or "Unknown"
        loc   = r["call_metadata"].get("location") or ""
        key   = f"{agent} ({loc})" if loc else agent
        agent_calls[key] += 1

        norm   = normalize_analysis(r["analysis"])
        scores = norm.get("agent_scores", {})
        for dim, score in scores.items():
            if isinstance(score, (int, float)):
                agent_scores[key][dim].append(score)
        agent_outcomes[key].append(norm.get("outcome", "unclear"))

    scorecards = {}
    for agent, dims in agent_scores.items():
        scorecards[agent] = {
            dim: round(sum(vals) / len(vals), 1)
            for dim, vals in dims.items()
        }
        outcomes = agent_outcomes[agent]
        scorecards[agent]["total_calls"]     = agent_calls[agent]
        scorecards[agent]["conversion_rate"] = round(
            outcomes.count("converted") / len(outcomes) * 100, 1
        ) if outcomes else 0

    # ── Distributions ──
    intents    = Counter(normalize_analysis(r["analysis"]).get("customer_intent")   for r in valid)
    outcomes   = Counter(normalize_analysis(r["analysis"]).get("outcome")           for r in valid)
    sentiments = Counter(normalize_analysis(r["analysis"]).get("customer_sentiment") for r in valid)

    # ── Collect patterns for LLM synthesis ──
    norms    = [normalize_analysis(r["analysis"]) for r in valid]
    wins     = [m for n in norms for m in n.get("winning_moments", [])]
    losses   = [m for n in norms for m in n.get("losing_moments", [])]
    missed   = [m for n in norms for m in n.get("missed_opportunities", [])]
    objs     = [o for n in norms for o in n.get("objections_raised", [])]
    pains    = [p for n in norms for p in n.get("customer_pain_points", [])]
    cues     = [c for n in norms for c in n.get("coaching_cues", [])]
    # Customer voice signals
    top_asks = [a for n in norms for a in n.get("top_asks", [])]
    issues   = [i for n in norms for i in n.get("issues_raised", [])]
    gaps     = [g for n in norms for g in n.get("product_service_gaps", [])]
    unmet    = [u for n in norms for u in n.get("unmet_needs", [])]

    # Compact summary for synthesis prompt (avoid huge token count)
    synthesis_input = {
        "total_calls": len(valid),
        "agent_scorecards": scorecards,
        "outcome_distribution": dict(outcomes),
        "intent_distribution": dict(intents),
        "sentiment_distribution": dict(sentiments),
        "winning_moments_sample":  wins[:60],
        "losing_moments_sample":   losses[:60],
        "missed_opportunities":    missed[:50],
        "objections_raised":       objs[:80],
        "customer_pain_points":    pains[:60],
        "coaching_cues":           cues[:60],
        # Customer voice — top asks, issues, gaps
        "customer_top_asks":       top_asks[:100],
        "customer_issues_raised":  issues[:100],
        "product_service_gaps":    gaps[:100],
        "unmet_needs":             unmet[:60],
    }

    print("  Calling Claude for pattern synthesis ...", flush=True)
    synthesis = call_claude(
        AGGREGATE_PROMPT.format(
            n=len(valid),
            data=json.dumps(synthesis_input, indent=2)
        ),
        client,
        max_tokens=4096,
    )

    return {
        "generated_at":           datetime.now().isoformat(timespec="seconds"),
        "total_calls":            len(valid),
        "agent_scorecards":       scorecards,
        "intent_distribution":    dict(intents),
        "outcome_distribution":   dict(outcomes),
        "sentiment_distribution": dict(sentiments),
        "raw_objections":         objs,
        "raw_pain_points":        pains,
        "raw_wins":               wins,
        "raw_losses":             losses,
        "synthesis":              synthesis,
    }


def save_report_txt(report: dict, path: str):
    """Write a human-readable plain-text report."""
    s = report["synthesis"]
    lines = [
        "=" * 70,
        "  WOODENSTREET SALES CALL INTELLIGENCE REPORT",
        f"  Generated: {report['generated_at']}",
        f"  Calls analyzed: {report['total_calls']}",
        "=" * 70,
        "",
        "── EXECUTIVE SUMMARY " + "─" * 49,
        s.get("executive_summary", ""),
        "",
        "── OUTCOME DISTRIBUTION " + "─" * 45,
    ]
    for outcome, count in sorted(report["outcome_distribution"].items(),
                                  key=lambda x: -x[1]):
        lines.append(f"  {str(outcome or 'unknown'):25s}: {count}")

    lines += ["", "── INTENT DISTRIBUTION " + "─" * 46]
    for intent, count in sorted(report["intent_distribution"].items(),
                                 key=lambda x: -x[1]):
        lines.append(f"  {str(intent or 'unknown'):25s}: {count}")

    lines += ["", "── AGENT SCORECARDS (avg scores / 10) " + "─" * 31]
    sorted_agents = sorted(
        report["agent_scorecards"].items(),
        key=lambda x: x[1].get("overall", x[1].get("empathy_tone", 0)),
        reverse=True,
    )
    header = f"  {'Agent':<28} {'Open':>4} {'Disc':>4} {'Prod':>4} {'Obj':>4} {'Close':>5} {'Emp':>4} {'Conv%':>6} {'Calls':>5}"
    lines.append(header)
    lines.append("  " + "-" * 66)
    for agent, sc in sorted_agents:
        lines.append(
            f"  {agent:<28} "
            f"{sc.get('opening_greeting',0):>4.1f} "
            f"{sc.get('needs_discovery',0):>4.1f} "
            f"{sc.get('product_knowledge',0):>4.1f} "
            f"{sc.get('objection_handling',0):>4.1f} "
            f"{sc.get('closing_attempt',0):>5.1f} "
            f"{sc.get('empathy_tone',0):>4.1f} "
            f"{sc.get('conversion_rate',0):>5.1f}% "
            f"{sc.get('total_calls',0):>5}"
        )

    lines += ["", "── TOP 10 CUSTOMER ASKS " + "─" * 45]
    for i, item in enumerate(s.get("top_10_customer_asks", []), 1):
        lines.append(f"  {i:2}. {item}")

    lines += ["", "── TOP 10 CUSTOMER ISSUES " + "─" * 43]
    for i, item in enumerate(s.get("top_10_issues", []), 1):
        lines.append(f"  {i:2}. {item}")

    lines += ["", "── TOP 10 PRODUCT / SERVICE GAPS " + "─" * 36]
    for i, item in enumerate(s.get("top_10_gaps", []), 1):
        lines.append(f"  {i:2}. {item}")

    lines += ["", "── TOP WINNING PATTERNS " + "─" * 45]
    for i, p in enumerate(s.get("top_winning_patterns", []), 1):
        lines.append(f"  {i}. {p.get('pattern', '')}")
        lines.append(f"     Why: {p.get('why_it_works', '')}")
        lines.append(f"     Example: {p.get('example', '')}")
        lines.append("")

    lines += ["── TOP LOSING PATTERNS " + "─" * 46]
    for i, p in enumerate(s.get("top_losing_patterns", []), 1):
        lines.append(f"  {i}. {p.get('pattern', '')}")
        lines.append(f"     Impact: {p.get('impact', '')}")
        lines.append(f"     Example: {p.get('example', '')}")
        lines.append("")

    lines += ["── TOP OBJECTIONS " + "─" * 51]
    for p in s.get("top_objections", []):
        lines.append(f"  • [{p.get('frequency','').upper()}] {p.get('objection','')}")
        lines.append(f"    Best response: {p.get('best_response','')}")
        lines.append("")

    lines += ["── WHAT TO TEACH AGENTS " + "─" * 45]
    for i, item in enumerate(s.get("what_to_teach_agents", []), 1):
        lines.append(f"  {i}. {item.get('skill','')} — {item.get('why','')}")
        lines.append(f"     How: {item.get('how','')}")
        lines.append("")

    lines += ["── SALES IMPROVEMENT FOCUS " + "─" * 42]
    for i, item in enumerate(s.get("sales_improvement_focus", []), 1):
        lines.append(f"  {i}. {item.get('area','')}")
        lines.append(f"     Now:    {item.get('current_state','')}")
        lines.append(f"     Target: {item.get('target','')}")
        lines.append("")

    lines += ["── AGENTS NEEDING COACHING " + "─" * 42]
    for item in s.get("agents_needing_coaching", []):
        lines.append(f"  • {item.get('agent','')} [{item.get('priority','').upper()}]: {item.get('weakness','')}")

    lines += ["", "── REVENUE LEAKAGE SIGNALS " + "─" * 42]
    for item in s.get("revenue_leakage_signals", []):
        lines.append(f"  • {item}")

    lines += ["", "=" * 70]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Analyze WoodenStreet call transcripts")
    parser.add_argument("--limit",        type=int, default=0,
                        help="Process at most N files (0 = all)")
    parser.add_argument("--workers",      type=int, default=5,
                        help="Parallel workers for real-time mode (default 5)")
    parser.add_argument("--batch",        action="store_true",
                        help="Use Anthropic Batch API — 50%% cheaper, async. "
                             "Submits all calls in one request, polls until done.")
    parser.add_argument("--batch-resume", default=None, metavar="BATCH_ID",
                        help="Resume polling a previously submitted batch by ID "
                             "(reads mapping from batch_state.json)")
    parser.add_argument("--reanalyze",    action="store_true",
                        help="Re-analyze files that already have analysis JSON")
    parser.add_argument("--report-only",  action="store_true",
                        help="Skip per-call analysis, only regenerate aggregate report")
    parser.add_argument("--from-date",    default=None,
                        help="Only analyze transcripts on or after this date (YYYY-MM-DD). "
                             "Older files are silently skipped.")
    args = parser.parse_args()

    os.makedirs(ANALYSIS_DIR, exist_ok=True)

    import anthropic
    api_key = load_api_key()
    client  = anthropic.Anthropic(api_key=api_key)

    # --- OpenAI (commented out) ---
    # import openai
    # api_key = load_api_key()
    # client  = openai.OpenAI(api_key=api_key)

    # Collect transcript files — flat (legacy) + dated subdirs
    all_jsons = sorted(
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")) +
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "????-??-??", "*_diarized.json"))
    )
    print(f"\nFound {len(all_jsons)} transcript files in {TRANSCRIPTS_DIR}")

    if not args.report_only:
        # Determine which to process — filter out too-short files upfront
        to_process   = []
        skipped_short = 0
        skipped_old   = 0
        for jf in all_jsons:
            stem = Path(jf).stem.replace("_diarized", "")

            # Route output to analysis/YYYY-MM-DD/ subfolder by call date
            fname_meta = parse_filename(jf)
            call_date  = (fname_meta.get("timestamp") or "")[:10]  # YYYY-MM-DD

            # Skip files before --from-date cutoff
            if args.from_date and call_date and call_date < args.from_date:
                skipped_old += 1
                continue

            day_dir  = os.path.join(ANALYSIS_DIR, call_date) if call_date else ANALYSIS_DIR
            out_path = os.path.join(day_dir, f"{stem}_analysis.json")

            # Backward-compat: also check old flat location
            old_path = os.path.join(ANALYSIS_DIR, f"{stem}_analysis.json")
            if (os.path.exists(out_path) or os.path.exists(old_path)) and not args.reanalyze:
                continue
            # Check word count before queuing — avoid API cost on noise calls
            try:
                with open(jf) as f:
                    meta_words = json.load(f)["metadata"]["total_words"]
                if meta_words < MIN_WORDS:
                    skipped_short += 1
                    continue
            except Exception:
                pass  # if unreadable, let analyze_one handle it
            to_process.append((jf, out_path, day_dir))

        if skipped_old:
            print(f"Skipped {skipped_old} files before {args.from_date} (--from-date filter)")

        if skipped_short:
            print(f"Skipped {skipped_short} files with < {MIN_WORDS} words (noise/dropped calls)")

        if args.limit:
            to_process = to_process[:args.limit]

        total = len(to_process)
        print(f"Files to analyze : {total}{' (limited)' if args.limit else ''}")

        # ── Batch resume: poll an already-submitted batch ────────────────────
        if args.batch_resume:
            state = load_batch_state()
            if not state:
                print("ERROR: batch_state.json not found — cannot resume.")
                sys.exit(1)
            batch_id = args.batch_resume
            id_map   = state["id_map"]
            print(f"Resuming batch {batch_id} ({len(id_map)} requests) ...")
            poll_and_save(batch_id, id_map, client)

        # ── Batch mode: submit all at once (50% cheaper) ─────────────────────
        elif args.batch:
            print("Mode: Batch API (50% cheaper)\n")
            run_batch_mode(to_process, client)

        # ── Real-time mode: parallel workers ─────────────────────────────────
        else:
            print(f"Mode: Real-time  |  Workers: {args.workers}\n")
            done_count  = 0
            error_count = 0

            def process_one(task):
                jf, out_path, day_dir = task
                result = analyze_one(jf, client)
                return jf, out_path, day_dir, result

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(process_one, task): task for task in to_process}
                for future in as_completed(futures):
                    done_count += 1
                    try:
                        jf, out_path, day_dir, result = future.result()
                        if result:
                            os.makedirs(day_dir, exist_ok=True)
                            with open(out_path, "w", encoding="utf-8") as f:
                                json.dump(result, f, indent=2, ensure_ascii=False)
                            print(f"  [{done_count}/{total}] ✓  "
                                  f"{Path(day_dir).name}/{Path(out_path).name}", flush=True)
                        else:
                            print(f"  [{done_count}/{total}] –  skipped {Path(jf).name}",
                                  flush=True)
                    except Exception as e:
                        error_count += 1
                        print(f"  [{done_count}/{total}] ✗  ERROR "
                              f"{Path(futures[future][0]).name}: {e}", flush=True)

            if error_count:
                print(f"\n  {error_count} failed — re-run to retry")

    # ── Aggregate report ──
    all_analysis_files = sorted(
        glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")) +           # flat (legacy)
        glob.glob(os.path.join(ANALYSIS_DIR, "????-??-??", "*_analysis.json"))  # dated subdirs
    )
    print(f"\nLoading {len(all_analysis_files)} analysis files for aggregation ...")

    all_results = []
    for af in all_analysis_files:
        with open(af) as f:
            all_results.append(json.load(f))

    if not all_results:
        print("No analysis files found — run without --report-only first.")
        return

    report = build_aggregate_report(all_results, client)

    report_json = os.path.join(ANALYSIS_DIR, "insights_report.json")
    report_txt  = os.path.join(ANALYSIS_DIR, "insights_report.txt")

    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    save_report_txt(report, report_txt)

    print(f"\n{'='*65}")
    print(f"  ANALYSIS COMPLETE")
    print(f"{'='*65}")
    print(f"  Per-call analyses : {ANALYSIS_DIR}/")
    print(f"  Insights report   : {report_txt}")
    print(f"  JSON report       : {report_json}")
    print(f"  Calls analyzed    : {report['total_calls']}")
    print()


if __name__ == "__main__":
    main()
