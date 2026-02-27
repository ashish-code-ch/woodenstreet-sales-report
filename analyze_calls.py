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

import os, sys, json, re, glob, argparse
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = "/home/user/Documents/AI/SalesScorecard"
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")
ANALYSIS_DIR    = os.path.join(BASE_DIR, "analysis")
KEY_FILE        = os.path.join(BASE_DIR, "anthropic_key.txt")
MODEL           = "claude-sonnet-4-6"
MIN_WORDS       = 20    # skip near-empty calls
# ─────────────────────────────────────────────────────────────────────────────


# ── API key ───────────────────────────────────────────────────────────────────
def load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if os.path.isfile(KEY_FILE):
        key = open(KEY_FILE).read().strip()
        if key:
            return key
    print("ERROR: No Anthropic API key found.")
    print(f"  Option 1: export ANTHROPIC_API_KEY=your_key")
    print(f"  Option 2: save your key to: {KEY_FILE}")
    sys.exit(1)


# ── Filename metadata parser ──────────────────────────────────────────────────
def parse_filename(filepath: str) -> dict:
    """Extract timestamp, agent name, location, phone from filename."""
    stem = Path(filepath).stem.replace("_diarized", "")

    ts_match = re.match(r"(\d{8})-(\d{6})", stem)
    timestamp = None
    if ts_match:
        d, t = ts_match.group(1), ts_match.group(2)
        timestamp = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"

    agent_match = re.search(r"BD Sales - (.+?)-all", stem)
    agent_name, location = None, None
    if agent_match:
        parts = agent_match.group(1).strip().rsplit(" ", 1)
        agent_name = parts[0] if len(parts) >= 1 else None
        location   = parts[1] if len(parts) == 2 else None

    phone_match = re.search(r"_(\d{8,15})_", stem)
    phone = phone_match.group(1) if phone_match else None

    return {
        "timestamp":     timestamp,
        "agent_name":    agent_name,
        "location":      location,
        "customer_phone": phone,
    }


# ── Transcript formatter ──────────────────────────────────────────────────────
def format_transcript(turns: list) -> str:
    """Format speaker turns as readable AGENT / CUSTOMER dialogue."""
    lines = []
    # Determine which speaker label maps to agent vs customer
    # (SPEAKER_00 is typically the agent who answers first)
    for t in turns:
        role = "AGENT" if t["speaker"] == "SPEAKER_00" else "CUSTOMER"
        lines.append(f"{role}: {t['text']}")
    return "\n".join(lines)


# ── Per-call analysis prompt ──────────────────────────────────────────────────
PER_CALL_PROMPT = """\
You are an expert sales quality analyst for WoodenStreet, an Indian furniture brand.
Analyze the call transcript below and return a JSON analysis.

Context: WoodenStreet agents handle inbound/outbound calls for store visits, product
queries, complaints, returns, order tracking, and sales (BD = Business Development).

Transcript:
{transcript}

Return ONLY valid JSON with this exact structure (no markdown, no extra text):
{{
  "call_summary": "2-3 sentence factual summary",
  "customer_intent": "buying | browsing | complaint | return_exchange | delivery_query | info_query | store_visit | other",
  "customer_need": "one sentence describing what the customer specifically wanted",
  "customer_sentiment": "positive | neutral | frustrated | angry | satisfied",
  "sentiment_trajectory": "stable_positive | stable_neutral | stable_negative | improved | declined | mixed",

  "conversation_flow": {{
    "opening":          "good | average | poor",
    "needs_discovery":  "good | average | poor | skipped",
    "product_pitch":    "good | average | poor | not_applicable",
    "objection_handling": "good | average | poor | no_objections",
    "closing":          "good | average | poor | not_attempted"
  }},

  "objections_raised": ["list each objection the customer raised"],
  "objection_responses": "brief note on how agent responded to objections",

  "agent_scores": {{
    "opening_greeting":   <1-10>,
    "needs_discovery":    <1-10>,
    "product_knowledge":  <1-10>,
    "objection_handling": <1-10>,
    "closing_attempt":    <1-10>,
    "empathy_tone":       <1-10>
  }},

  "outcome": "converted | follow_up_scheduled | lost | support_resolved | no_answer | unclear",
  "outcome_reasoning": "one sentence explaining why",

  "winning_moments": ["concrete things the agent did well — quote or describe"],
  "losing_moments":  ["concrete things the agent missed or did poorly"],
  "missed_opportunities": ["upsell, cross-sell, or closing chances the agent let pass"],

  "customer_pain_points": ["specific friction points, complaints, or frustrations"],
  "feature_requests": ["any product or service features the customer asked about"],
  "pricing_sensitivity": "high | medium | low | not_discussed",
  "delivery_complaint": true or false,

  "coaching_cues": ["3-5 specific, actionable coaching tips for this agent"],
  "key_quote_agent":    "most revealing thing the agent said (exact quote)",
  "key_quote_customer": "most revealing thing the customer said (exact quote)"
}}"""


# ── Aggregate synthesis prompt ────────────────────────────────────────────────
AGGREGATE_PROMPT = """\
You are analyzing a batch of WoodenStreet sales call analyses to produce team-level insights.

Below is a JSON summary of all {n} analyzed calls including agent scores, outcomes,
objections, pain points, winning moments, and losing moments.

Data:
{data}

Return ONLY valid JSON with this structure (no markdown):
{{
  "executive_summary": "3-4 sentence overview of the team's performance",

  "top_winning_patterns": [
    {{"pattern": "description", "why_it_works": "explanation", "example": "from data"}}
  ],
  "top_losing_patterns": [
    {{"pattern": "description", "impact": "what it costs", "example": "from data"}}
  ],
  "top_objections": [
    {{"objection": "description", "frequency": "high/medium/low", "best_response": "suggested response"}}
  ],
  "top_customer_pain_points": ["list of most common friction points"],
  "revenue_leakage_signals": ["missed upsell/cross-sell patterns across calls"],

  "what_to_teach_agents": [
    {{"skill": "skill name", "why": "reason", "how": "specific training approach"}}
  ],
  "sales_improvement_focus": [
    {{"area": "focus area", "current_state": "what's happening", "target": "what good looks like"}}
  ],
  "top_agents_by_score": ["agent names ranked by overall score"],
  "agents_needing_coaching": [
    {{"agent": "name", "weakness": "main gap", "priority": "high/medium"}}
  ]
}}"""


# ── Claude API call ───────────────────────────────────────────────────────────
def call_claude(prompt: str, client, max_tokens: int = 1800) -> dict:
    """Call Claude and parse JSON response."""
    message = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


# ── Per-call processing ───────────────────────────────────────────────────────
def analyze_one(json_path: str, client) -> dict | None:
    with open(json_path) as f:
        data = json.load(f)

    meta  = data["metadata"]
    turns = data["turns"]

    if meta["total_words"] < MIN_WORDS:
        print(f"  SKIP — too short ({meta['total_words']} words)", flush=True)
        return None

    transcript_text = format_transcript(turns)
    filename_meta   = parse_filename(json_path)

    analysis = call_claude(
        PER_CALL_PROMPT.format(transcript=transcript_text), client
    )

    return {
        "file": os.path.basename(json_path),
        "call_metadata": {
            **filename_meta,
            "duration_sec":  meta.get("duration_sec"),
            "total_words":   meta["total_words"],
            "num_turns":     meta["num_turns"],
            "language":      meta.get("language"),
            "processed_at":  meta.get("processed_at"),
        },
        "analysis":    analysis,
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
    }


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
        scores = r["analysis"].get("agent_scores", {})
        for dim, score in scores.items():
            if isinstance(score, (int, float)):
                agent_scores[key][dim].append(score)
        agent_outcomes[key].append(r["analysis"].get("outcome", "unclear"))

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
    intents   = Counter(r["analysis"].get("customer_intent")  for r in valid)
    outcomes  = Counter(r["analysis"].get("outcome")          for r in valid)
    sentiments= Counter(r["analysis"].get("customer_sentiment") for r in valid)

    # ── Collect patterns for LLM synthesis ──
    wins     = [m for r in valid for m in r["analysis"].get("winning_moments", [])]
    losses   = [m for r in valid for m in r["analysis"].get("losing_moments", [])]
    missed   = [m for r in valid for m in r["analysis"].get("missed_opportunities", [])]
    objs     = [o for r in valid for o in r["analysis"].get("objections_raised", [])]
    pains    = [p for r in valid for p in r["analysis"].get("customer_pain_points", [])]
    cues     = [c for r in valid for c in r["analysis"].get("coaching_cues", [])]
    features = [f for r in valid for f in r["analysis"].get("feature_requests", [])]

    # Compact summary for synthesis prompt (avoid huge token count)
    synthesis_input = {
        "total_calls": len(valid),
        "agent_scorecards": scorecards,
        "outcome_distribution": dict(outcomes),
        "intent_distribution": dict(intents),
        "sentiment_distribution": dict(sentiments),
        "winning_moments_sample":  wins[:80],
        "losing_moments_sample":   losses[:80],
        "missed_opportunities":    missed[:60],
        "objections_raised":       objs[:100],
        "customer_pain_points":    pains[:80],
        "coaching_cues":           cues[:80],
        "feature_requests":        features[:40],
    }

    print("  Calling Claude for pattern synthesis ...", flush=True)
    synthesis = call_claude(
        AGGREGATE_PROMPT.format(
            n=len(valid),
            data=json.dumps(synthesis_input, indent=2)
        ),
        client,
        max_tokens=3000,
    )

    return {
        "generated_at":       datetime.now().isoformat(timespec="seconds"),
        "total_calls":        len(valid),
        "agent_scorecards":   scorecards,
        "intent_distribution":    dict(intents),
        "outcome_distribution":   dict(outcomes),
        "sentiment_distribution": dict(sentiments),
        "raw_objections":     objs,
        "raw_pain_points":    pains,
        "raw_wins":           wins,
        "raw_losses":         losses,
        "synthesis":          synthesis,
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
        lines.append(f"  {outcome:25s}: {count}")

    lines += ["", "── INTENT DISTRIBUTION " + "─" * 46]
    for intent, count in sorted(report["intent_distribution"].items(),
                                 key=lambda x: -x[1]):
        lines.append(f"  {intent:25s}: {count}")

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
    parser.add_argument("--limit",       type=int, default=0,
                        help="Process at most N files (0 = all)")
    parser.add_argument("--reanalyze",   action="store_true",
                        help="Re-analyze files that already have analysis JSON")
    parser.add_argument("--report-only", action="store_true",
                        help="Skip per-call analysis, only regenerate aggregate report")
    args = parser.parse_args()

    os.makedirs(ANALYSIS_DIR, exist_ok=True)

    import anthropic
    api_key = load_api_key()
    client  = anthropic.Anthropic(api_key=api_key)

    # Collect transcript files
    all_jsons = sorted(glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")))
    print(f"\nFound {len(all_jsons)} transcript files in {TRANSCRIPTS_DIR}")

    if not args.report_only:
        # Determine which to process
        to_process = []
        for jf in all_jsons:
            stem     = Path(jf).stem.replace("_diarized", "")
            out_path = os.path.join(ANALYSIS_DIR, f"{stem}_analysis.json")
            if os.path.exists(out_path) and not args.reanalyze:
                continue
            to_process.append((jf, out_path))

        if args.limit:
            to_process = to_process[:args.limit]

        print(f"Files to analyze: {len(to_process)}"
              f"{' (limited)' if args.limit else ''}\n")

        for i, (jf, out_path) in enumerate(to_process, 1):
            name = Path(jf).name
            print(f"[{i}/{len(to_process)}] {name}", flush=True)
            try:
                result = analyze_one(jf, client)
                if result:
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, indent=2, ensure_ascii=False)
                    print(f"  → saved {Path(out_path).name}", flush=True)
                else:
                    print(f"  → skipped", flush=True)
            except Exception as e:
                print(f"  ERROR: {e}", flush=True)
                import traceback; traceback.print_exc()

    # ── Aggregate report ──
    all_analysis_files = sorted(
        glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json"))
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
