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
from json_repair import repair_json
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

from system_prompt import get_claude_cached_system
from agents import resolve_agent

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = "/home/user/Documents/AI/SalesScorecard"
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")
ANALYSIS_DIR    = os.path.join(BASE_DIR, "analysis")
KEY_FILE        = os.path.join(BASE_DIR, "anthropic_key.txt")
# OPENAI_KEY_FILE = os.path.join(BASE_DIR, "OpenAI_Woodenstreet_key")  # OpenAI — commented out
MODEL           = "claude-3-haiku-20240307"
MIN_WORDS       = 100   # skip near-empty calls (< 100 words = noise/wrong number/dropped)
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
    Uses agents.py lookup to resolve short names to full agent details."""
    stem = Path(filepath).stem.replace("_diarized", "")

    ts_match = re.match(r"(\d{8})-(\d{6})", stem)
    timestamp = None
    if ts_match:
        d, t = ts_match.group(1), ts_match.group(2)
        timestamp = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"

    phone_match = re.search(r"_(\d{8,15})_", stem)
    phone = phone_match.group(1) if phone_match else None

    # Extract short name from filename e.g. "BD Sales - Sachin JPR"
    agent_match = re.search(r"BD Sales - (.+?)-all", stem)
    short_name = agent_match.group(1).strip() if agent_match else None

    # Resolve to full agent details via lookup table
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
    """Call Claude and parse JSON response."""
    kwargs = dict(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system_content is not None:
        kwargs["system"] = system_content

    # --- OpenAI (commented out) ---
    # response = client.chat.completions.create(
    #     model=MODEL, max_tokens=max_tokens,
    #     messages=[{"role": "user", "content": prompt}],
    # )
    # raw = response.choices[0].message.content.strip()

    # --- Anthropic ---
    message = client.messages.create(**kwargs)
    raw = message.content[0].text.strip()

    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Truncated or malformed JSON — attempt repair
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, dict) and repaired:
            return repaired
        print("  WARNING: Could not parse JSON response, returning empty dict")
        return {}


# ── Per-call processing ───────────────────────────────────────────────────────
def analyze_one(json_path: str, client) -> dict | None:
    with open(json_path) as f:
        data = json.load(f)

    meta  = data["metadata"]
    turns = data["turns"]
    transcript_text = format_transcript(turns)
    filename_meta   = parse_filename(json_path)

    # System prompt is cached — only billed once per cache TTL (~5 min)
    analysis = call_claude(
        f"Analyze this call transcript:\n\n{transcript_text}",
        client,
        system_content=get_claude_cached_system(),
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

    # --- OpenAI (commented out) ---
    # import openai
    # api_key = load_api_key()
    # client  = openai.OpenAI(api_key=api_key)

    # Collect transcript files
    all_jsons = sorted(glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")))
    print(f"\nFound {len(all_jsons)} transcript files in {TRANSCRIPTS_DIR}")

    if not args.report_only:
        # Determine which to process — filter out too-short files upfront
        to_process = []
        skipped_short = 0
        for jf in all_jsons:
            stem     = Path(jf).stem.replace("_diarized", "")
            out_path = os.path.join(ANALYSIS_DIR, f"{stem}_analysis.json")
            if os.path.exists(out_path) and not args.reanalyze:
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
            to_process.append((jf, out_path))

        if skipped_short:
            print(f"Skipped {skipped_short} files with < {MIN_WORDS} words (noise/dropped calls)")

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
