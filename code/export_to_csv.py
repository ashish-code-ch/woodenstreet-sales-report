#!/usr/bin/env python3
"""
Export all analysis JSONs → flat CSVs for Power BI.

Runs daily AFTER analyze_calls.py. Writes three tiers of exports:

  exports/YYYY-MM/        — monthly  (e.g. exports/2026-03/)
  exports/YYYY-QN/        — quarterly (e.g. exports/2026-Q1/)
  exports/master/         — all-time union  ← Power BI / GitHub Pages connects here

Each folder contains:
  calls.csv           — one row per call (fact table)
  customer_voice.csv  — one row per customer ask / issue / gap / feedback
  coaching.csv        — one row per strength / improvement / missed opportunity
  agent_scorecard.csv — one row per agent per period (aggregated)

master/ additionally contains:
  daily_activity.csv  — one row per agent per date (from ozonetel_archive stats)

Usage:
  python export_to_csv.py
"""

import os, json, glob, csv
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE_DIR     = "/home/user/Documents/AI/SalesScorecard"
ANALYSIS_DIR = os.path.join(BASE_DIR, "analysis")
EXPORT_DIR   = os.path.join(BASE_DIR, "exports")
ARCHIVE_DIR  = os.path.join(BASE_DIR, "ozonetel_archive")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_all_analyses():
    """Load from both flat analysis/ (legacy) and analysis/YYYY-MM-DD/ subdirs."""
    files = sorted(
        glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")) +
        glob.glob(os.path.join(ANALYSIS_DIR, "????-??-??", "*_analysis.json"))
    )
    records = []
    seen = set()
    for f in files:
        name = Path(f).name
        if name in seen:
            continue  # skip duplicates if a file exists in both flat and dated locations
        seen.add(name)
        try:
            records.append(json.load(open(f, encoding="utf-8")))
        except Exception as e:
            print(f"  SKIP {f}: {e}")
    return records


def safe(val, default=""):
    if val is None:
        return default
    return str(val).strip()


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def parse_month(timestamp_str):
    """Return YYYY-MM from a timestamp string like '2026-03-19 10:46:44'."""
    try:
        return timestamp_str[:7]
    except Exception:
        return "unknown"


def get_quarter(month_str):
    """'2026-03' → '2026-Q1',  '2026-07' → '2026-Q3'."""
    try:
        y, m = month_str.split("-")
        q = (int(m) - 1) // 3 + 1
        return f"{y}-Q{q}"
    except Exception:
        return "unknown"


# ── Normalize across old and new schema ───────────────────────────────────────

def flatten_call(record):
    """Return a flat dict for one analysis record (handles old + new schema)."""
    meta     = record.get("call_metadata", {})
    analysis = record.get("analysis", {})

    timestamp = safe(meta.get("timestamp"))
    date      = timestamp[:10] if timestamp else ""
    month     = timestamp[:7]  if timestamp else ""
    quarter   = get_quarter(month) if month else ""

    agent     = safe(meta.get("agent_name") or meta.get("agent_short"), "Unknown")
    location  = safe(meta.get("location"), "Unknown")
    team      = safe(meta.get("team"), "BD Sales")
    status    = safe(meta.get("agent_status"), "Unknown")
    phone     = safe(meta.get("customer_phone"))
    duration  = safe_float(meta.get("duration_sec"))
    words     = int(meta.get("total_words", 0))
    turns     = int(meta.get("num_turns", 0))

    # ── New schema (agent_scorecard block) ────────────────────────────────────
    if "agent_scorecard" in analysis:
        sc   = analysis.get("agent_scorecard", {})
        int_ = analysis.get("intent", {})
        sen  = analysis.get("sentiment", {})
        out  = analysis.get("call_outcome", {})
        comp = analysis.get("compliance", {})
        tr   = analysis.get("talk_ratio", {})

        outcome_map = {
            "sale_converted":      "Converted",
            "store_visit_booked":  "Store Visit",
            "follow_up_scheduled": "Follow-up",
            "complaint_resolved":  "Resolved",
            "partially_resolved":  "Partial",
            "transferred":         "Transferred",
            "unresolved":          "Lost",
        }
        resolution = safe(out.get("resolution_type"), "unclear")

        return {
            # Identity
            "call_id":        safe(record.get("file", "")),
            "timestamp":      timestamp,
            "date":           date,
            "month":          month,
            "quarter":        quarter,
            "agent_name":     agent,
            "location":       location,
            "team":           team,
            "agent_status":   status,
            "customer_phone": phone,

            # Call basics
            "duration_sec":   duration,
            "total_words":    words,
            "num_turns":      turns,

            # Intent
            "primary_intent":        safe(int_.get("primary_intent")),
            "sub_intent":            safe(int_.get("sub_intent")),
            "urgency_level":         safe(int_.get("urgency_level")),
            "competitor_mentioned":  safe(int_.get("competitor_mentioned")),
            "competitor_switch":     int_.get("competitor_switch_intent", False),
            "upsell_signal":         int_.get("upsell_signal_detected", False),

            # Sentiment
            "sentiment_overall":  safe(sen.get("overall")),
            "sentiment_score":    safe_float(sen.get("score")),
            "opening_emotion":    safe(sen.get("opening_emotion")),
            "closing_emotion":    safe(sen.get("closing_emotion")),
            "churn_risk":         safe(sen.get("churn_risk")),

            # Agent scores
            "score_opening":       safe_float(sc.get("opening_greeting")),
            "score_discovery":     safe_float(sc.get("needs_discovery")),
            "score_product":       safe_float(sc.get("product_knowledge")),
            "score_objection":     safe_float(sc.get("objection_handling")),
            "score_closing":       safe_float(sc.get("closing_attempt")),
            "score_empathy":       safe_float(sc.get("empathy_tone")),
            "score_clarity":       safe_float(sc.get("communication_clarity")),
            "score_overall":       safe_float(sc.get("overall_score")),

            # Outcome
            "resolution_type":     outcome_map.get(resolution, resolution),
            "resolved":            out.get("resolved", False),
            "follow_up_required":  out.get("follow_up_required", False),

            # Talk ratio
            "agent_talk_pct":    safe_float(tr.get("agent_percent")),
            "customer_talk_pct": safe_float(tr.get("customer_percent")),

            # Compliance
            "unauthorized_promise": comp.get("unauthorized_promise_made", False),
            "wrong_policy_info":    comp.get("wrong_policy_info_given", False),

            # Summary
            "call_summary": safe(analysis.get("call_summary")),
        }

    # ── Old schema fallback ───────────────────────────────────────────────────
    sc = analysis.get("agent_scores", {})
    outcome_map_old = {
        "converted":          "Converted",
        "follow_up_scheduled":"Follow-up",
        "support_resolved":   "Resolved",
        "lost":               "Lost",
    }

    return {
        "call_id":        safe(record.get("file", "")),
        "timestamp":      timestamp,
        "date":           date,
        "month":          month,
        "quarter":        quarter,
        "agent_name":     agent,
        "location":       location,
        "team":           team,
        "agent_status":   status,
        "customer_phone": phone,

        "duration_sec":   duration,
        "total_words":    words,
        "num_turns":      turns,

        "primary_intent":       safe(analysis.get("customer_intent")),
        "sub_intent":           "",
        "urgency_level":        "",
        "competitor_mentioned": "",
        "competitor_switch":    False,
        "upsell_signal":        False,

        "sentiment_overall":  safe(analysis.get("customer_sentiment")),
        "sentiment_score":    0.0,
        "opening_emotion":    "",
        "closing_emotion":    "",
        "churn_risk":         "",

        "score_opening":    safe_float(sc.get("opening_greeting")),
        "score_discovery":  safe_float(sc.get("needs_discovery")),
        "score_product":    safe_float(sc.get("product_knowledge")),
        "score_objection":  safe_float(sc.get("objection_handling")),
        "score_closing":    safe_float(sc.get("closing_attempt")),
        "score_empathy":    safe_float(sc.get("empathy_tone")),
        "score_clarity":    safe_float(sc.get("communication_clarity", 0)),
        "score_overall":    safe_float(sc.get("overall_score", 0)),

        "resolution_type":    outcome_map_old.get(safe(analysis.get("outcome")), safe(analysis.get("outcome"))),
        "resolved":           analysis.get("outcome") not in ("lost", "unclear"),
        "follow_up_required": analysis.get("outcome") == "follow_up_scheduled",

        "agent_talk_pct":    0.0,
        "customer_talk_pct": 0.0,

        "unauthorized_promise": False,
        "wrong_policy_info":    False,

        "call_summary": safe(analysis.get("call_summary")),
    }


# ── Export functions (all accept out_dir) ─────────────────────────────────────

def export_calls(records, out_dir):
    rows = [flatten_call(r) for r in records]
    if not rows:
        return 0
    path = os.path.join(out_dir, "calls.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_customer_voice(records, out_dir):
    rows = []
    for r in records:
        meta     = r.get("call_metadata", {})
        analysis = r.get("analysis", {})
        call_id  = safe(r.get("file", ""))
        agent    = safe(meta.get("agent_name") or meta.get("agent_short"), "Unknown")
        location = safe(meta.get("location"), "Unknown")
        date     = safe(meta.get("timestamp", ""))[:10]
        month    = safe(meta.get("timestamp", ""))[:7]
        quarter  = get_quarter(month) if month else ""

        cv = analysis.get("customer_voice", {})
        type_map = {
            "top_asks":             "Ask",
            "issues_raised":        "Issue",
            "product_service_gaps": "Gap",
            "process_service_gaps": "Gap",
            "unmet_needs":          "Unmet Need",
            "positive_feedback":    "Positive",
        }
        for field, label in type_map.items():
            for item in cv.get(field, []):
                if item and str(item).strip():
                    rows.append({
                        "call_id":   call_id,
                        "date":      date,
                        "month":     month,
                        "quarter":   quarter,
                        "agent_name": agent,
                        "location":  location,
                        "type":      label,
                        "text":      safe(item),
                    })

        # Old schema — pain points
        for item in analysis.get("customer_pain_points", []):
            if item and str(item).strip():
                rows.append({
                    "call_id":    call_id,
                    "date":       date,
                    "month":      month,
                    "quarter":    quarter,
                    "agent_name": agent,
                    "location":   location,
                    "type":       "Issue",
                    "text":       safe(item),
                })

    path = os.path.join(out_dir, "customer_voice.csv")
    fieldnames = ["call_id", "date", "month", "quarter", "agent_name", "location", "type", "text"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_coaching(records, out_dir):
    rows = []
    for r in records:
        meta     = r.get("call_metadata", {})
        analysis = r.get("analysis", {})
        call_id  = safe(r.get("file", ""))
        agent    = safe(meta.get("agent_name") or meta.get("agent_short"), "Unknown")
        location = safe(meta.get("location"), "Unknown")
        date     = safe(meta.get("timestamp", ""))[:10]
        month    = safe(meta.get("timestamp", ""))[:7]
        quarter  = get_quarter(month) if month else ""

        def add_items(items, label):
            for item in (items or []):
                if item and str(item).strip():
                    rows.append({
                        "call_id":    call_id,
                        "date":       date,
                        "month":      month,
                        "quarter":    quarter,
                        "agent_name": agent,
                        "location":   location,
                        "type":       label,
                        "text":       safe(item),
                    })

        sc = analysis.get("agent_scorecard", {})
        add_items(sc.get("strengths", []),         "Strength")
        add_items(sc.get("improvement_areas", []), "Improvement")
        tip = sc.get("coaching_tip", "")
        if tip:
            rows.append({"call_id": call_id, "date": date, "month": month, "quarter": quarter,
                         "agent_name": agent, "location": location,
                         "type": "Coaching Tip", "text": safe(tip)})
        missed = sc.get("missed_opportunity", "")
        if missed:
            rows.append({"call_id": call_id, "date": date, "month": month, "quarter": quarter,
                         "agent_name": agent, "location": location,
                         "type": "Missed Opportunity", "text": safe(missed)})

        # Old schema
        add_items(analysis.get("winning_moments", []),     "Strength")
        add_items(analysis.get("losing_moments", []),      "Improvement")
        add_items(analysis.get("missed_opportunities", []),"Missed Opportunity")
        add_items(analysis.get("coaching_cues", []),       "Coaching Tip")

    path = os.path.join(out_dir, "coaching.csv")
    fieldnames = ["call_id", "date", "month", "quarter", "agent_name", "location", "type", "text"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_agent_scorecard(records, out_dir, period_col="month"):
    """
    Pre-aggregated scorecard per agent per period.
    period_col: "month"   → groups by YYYY-MM,   period value e.g. "2026-03"
                "quarter" → groups by YYYY-QN,   period value e.g. "2026-Q1"
    """
    data        = defaultdict(lambda: defaultdict(list))
    calls_count = defaultdict(int)
    outcomes    = defaultdict(list)
    teams       = defaultdict(set)

    for r in records:
        flat = flatten_call(r)
        period = flat[period_col] if period_col in flat else flat["month"]
        key  = (flat["agent_name"], flat["location"], period)
        calls_count[key] += 1
        outcomes[key].append(flat["resolution_type"])
        teams[key].add(flat["team"])

        for dim in ["score_opening", "score_discovery", "score_product",
                    "score_objection", "score_closing", "score_empathy",
                    "score_clarity", "score_overall"]:
            v = flat[dim]
            if v > 0:
                data[key][dim].append(v)

    rows = []
    for key, dims in data.items():
        agent, location, period = key
        total = calls_count[key]
        out   = outcomes[key]
        converted   = out.count("Converted")
        store_visits = out.count("Store Visit")
        team = next(iter(teams[key]), "Unknown")

        row = {
            "agent_name":      agent,
            "location":        location,
            "team":            team,
            period_col:        period,
            "total_calls":     total,
            "converted":       converted,
            "conversion_rate": round(converted / total * 100, 1) if total else 0,
            "store_visits":    store_visits,
        }
        for dim, vals in dims.items():
            row[f"avg_{dim}"] = round(sum(vals) / len(vals), 2) if vals else 0.0

        rows.append(row)

    rows.sort(key=lambda x: (x[period_col], x["agent_name"]))

    if not rows:
        return 0

    path = os.path.join(out_dir, "agent_scorecard.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── Daily Activity Export (from ozonetel_archive stats) ───────────────────────

def export_daily_activity(out_dir):
    """
    Reads ALL ozonetel_archive/{date}/stats.json files.
    Produces daily_activity.csv — one row per agent per date.
    Covers ALL agents (not just analyzed ones) — source of truth for
    attendance, productivity, and lost-call tracking.
    """
    rows = []
    BD_TARGET_HRS = 3.0

    archive = Path(ARCHIVE_DIR)
    if not archive.exists():
        print("  WARN: ozonetel_archive/ not found — skipping daily_activity.csv")
        return 0

    for stats_file in sorted(archive.glob("*/stats.json")):
        date = stats_file.parent.name   # YYYY-MM-DD
        month   = date[:7]
        quarter = get_quarter(month)

        try:
            stats = json.loads(stats_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  SKIP {stats_file}: {e}")
            continue

        for agent_name, d in stats.items():
            if agent_name == "Unknown" or " -> " in agent_name:
                continue

            team     = d.get("team", "Unknown")
            talk_hrs = d.get("talk_hours", round(d.get("talk_seconds", 0) / 3600, 2))
            lost     = d.get("lost_calls", d.get("unanswered", 0) + d.get("dropped", 0))

            rows.append({
                "date":               date,
                "month":              month,
                "quarter":            quarter,
                "agent_name":         agent_name,
                "team":               team,
                "location":           d.get("locations", ""),
                "total_calls":        d.get("total_calls", 0),
                "answered":           d.get("answered", 0),
                "unanswered":         d.get("unanswered", 0),
                "dropped":            d.get("dropped", 0),
                "lost_calls":         lost,
                "lost_rate_pct":      d.get("lost_rate_pct",
                                        round(lost / d["total_calls"] * 100, 1)
                                        if d.get("total_calls") else 0),
                "talk_seconds":       d.get("talk_seconds", 0),
                "talk_hours":         talk_hrs,
                "avg_call_sec":       d.get("avg_talk_sec", 0),
                "recordings_downloaded": d.get("recordings_available", 0),
                "target_hours":       BD_TARGET_HRS if team == "BD Sales" else "",
                "below_3h_target":    (team == "BD Sales" and talk_hrs < BD_TARGET_HRS),
                "shortfall_hours":    round(BD_TARGET_HRS - talk_hrs, 2)
                                      if team == "BD Sales" and talk_hrs < BD_TARGET_HRS else 0,
            })

    if not rows:
        return 0

    rows.sort(key=lambda x: (x["date"], x["team"], x["agent_name"]))

    path = os.path.join(out_dir, "daily_activity.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    print(f"Loading analysis files from {ANALYSIS_DIR} ...")

    records = load_all_analyses()
    print(f"Loaded {len(records)} records\n")

    if not records:
        print("No analysis files found.")
        return

    # ── Group by month and quarter ────────────────────────────────────────────
    by_month   = defaultdict(list)
    by_quarter = defaultdict(list)
    for r in records:
        ts = r.get("call_metadata", {}).get("timestamp", "")
        m  = ts[:7] if ts else "unknown"
        q  = get_quarter(m)
        by_month[m].append(r)
        by_quarter[q].append(r)

    # ── Monthly exports ───────────────────────────────────────────────────────
    print("Monthly exports:")
    for month in sorted(by_month):
        recs    = by_month[month]
        out_dir = os.path.join(EXPORT_DIR, month)
        os.makedirs(out_dir, exist_ok=True)
        n_calls = export_calls(recs, out_dir)
        export_customer_voice(recs, out_dir)
        export_coaching(recs, out_dir)
        n_score = export_agent_scorecard(recs, out_dir, period_col="month")
        print(f"  {month}/  {n_calls} calls  {n_score} scorecard rows")

    # ── Quarterly exports ─────────────────────────────────────────────────────
    print("\nQuarterly exports:")
    for quarter in sorted(by_quarter):
        recs    = by_quarter[quarter]
        out_dir = os.path.join(EXPORT_DIR, quarter)
        os.makedirs(out_dir, exist_ok=True)
        n_calls = export_calls(recs, out_dir)
        export_customer_voice(recs, out_dir)
        export_coaching(recs, out_dir)
        n_score = export_agent_scorecard(recs, out_dir, period_col="quarter")
        print(f"  {quarter}/  {n_calls} calls  {n_score} scorecard rows")

    # ── Master (all-time union) ───────────────────────────────────────────────
    master_dir = os.path.join(EXPORT_DIR, "master")
    os.makedirs(master_dir, exist_ok=True)
    n_calls    = export_calls(records, master_dir)
    n_voice    = export_customer_voice(records, master_dir)
    n_coach    = export_coaching(records, master_dir)
    n_score    = export_agent_scorecard(records, master_dir, period_col="month")
    n_activity = export_daily_activity(master_dir)

    print(f"\nMaster exports (all-time):")
    print(f"  calls.csv           : {n_calls} rows")
    print(f"  customer_voice.csv  : {n_voice} rows")
    print(f"  coaching.csv        : {n_coach} rows")
    print(f"  agent_scorecard.csv : {n_score} rows (agent × month)")
    print(f"  daily_activity.csv  : {n_activity} rows (agent × date)")
    print(f"\nExports saved to: {EXPORT_DIR}/")
    print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
