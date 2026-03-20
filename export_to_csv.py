#!/usr/bin/env python3
"""
Export all analysis JSONs → flat CSVs for Power BI.

Runs daily AFTER analyze_calls.py. Rebuilds all CSVs from scratch
so Power BI always gets a complete, consistent dataset.

Output files (in /exports/):
  calls.csv          — one row per call (fact table)
  customer_voice.csv — one row per customer ask / issue / gap / feedback
  coaching.csv       — one row per strength / improvement / missed opportunity
  agent_scorecard.csv— one row per agent per month (pre-aggregated)

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_all_analyses():
    files = sorted(glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")))
    records = []
    for f in files:
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
    """Return YYYY-MM from a timestamp string like '2025-10-01 10:46:44'."""
    try:
        return timestamp_str[:7]
    except:
        return "unknown"


# ── Normalize across old and new schema ───────────────────────────────────────

def flatten_call(record):
    """Return a flat dict for one analysis record (handles old + new schema)."""
    meta     = record.get("call_metadata", {})
    analysis = record.get("analysis", {})

    timestamp = safe(meta.get("timestamp"))
    date      = timestamp[:10] if timestamp else ""
    month     = timestamp[:7]  if timestamp else ""

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


# ── Export functions ───────────────────────────────────────────────────────────

def export_calls(records):
    rows = [flatten_call(r) for r in records]
    if not rows:
        return 0

    path = os.path.join(EXPORT_DIR, "calls.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_customer_voice(records):
    rows = []
    for r in records:
        meta     = r.get("call_metadata", {})
        analysis = r.get("analysis", {})
        call_id  = safe(r.get("file", ""))
        agent    = safe(meta.get("agent_name") or meta.get("agent_short"), "Unknown")
        location = safe(meta.get("location"), "Unknown")
        date     = safe(meta.get("timestamp", ""))[:10]
        month    = safe(meta.get("timestamp", ""))[:7]

        cv = analysis.get("customer_voice", {})
        type_map = {
            "top_asks":             "Ask",
            "issues_raised":        "Issue",
            "product_service_gaps": "Gap",
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
                    "agent_name": agent,
                    "location":   location,
                    "type":       "Issue",
                    "text":       safe(item),
                })

    path = os.path.join(EXPORT_DIR, "customer_voice.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["call_id","date","month","agent_name","location","type","text"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_coaching(records):
    rows = []
    for r in records:
        meta     = r.get("call_metadata", {})
        analysis = r.get("analysis", {})
        call_id  = safe(r.get("file", ""))
        agent    = safe(meta.get("agent_name") or meta.get("agent_short"), "Unknown")
        location = safe(meta.get("location"), "Unknown")
        date     = safe(meta.get("timestamp", ""))[:10]
        month    = safe(meta.get("timestamp", ""))[:7]

        def add_items(items, label):
            for item in (items or []):
                if item and str(item).strip():
                    rows.append({
                        "call_id":    call_id,
                        "date":       date,
                        "month":      month,
                        "agent_name": agent,
                        "location":   location,
                        "type":       label,
                        "text":       safe(item),
                    })

        sc = analysis.get("agent_scorecard", {})
        add_items(sc.get("strengths", []),          "Strength")
        add_items(sc.get("improvement_areas", []),  "Improvement")
        tip = sc.get("coaching_tip", "")
        if tip:
            rows.append({"call_id": call_id, "date": date, "month": month,
                         "agent_name": agent, "location": location,
                         "type": "Coaching Tip", "text": safe(tip)})
        missed = sc.get("missed_opportunity", "")
        if missed:
            rows.append({"call_id": call_id, "date": date, "month": month,
                         "agent_name": agent, "location": location,
                         "type": "Missed Opportunity", "text": safe(missed)})

        # Old schema
        add_items(analysis.get("winning_moments", []),    "Strength")
        add_items(analysis.get("losing_moments", []),     "Improvement")
        add_items(analysis.get("missed_opportunities", []),"Missed Opportunity")
        add_items(analysis.get("coaching_cues", []),      "Coaching Tip")

    path = os.path.join(EXPORT_DIR, "coaching.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["call_id","date","month","agent_name","location","type","text"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_agent_scorecard(records):
    """Pre-aggregated per agent per month — for quick Power BI scorecard visuals."""
    # bucket: (agent, location, month) → lists of scores + outcomes
    data = defaultdict(lambda: defaultdict(list))
    calls_count = defaultdict(int)
    outcomes    = defaultdict(list)

    for r in records:
        flat = flatten_call(r)
        key  = (flat["agent_name"], flat["location"], flat["month"])
        calls_count[key] += 1
        outcomes[key].append(flat["resolution_type"])

        for dim in ["score_opening","score_discovery","score_product",
                    "score_objection","score_closing","score_empathy",
                    "score_clarity","score_overall"]:
            v = flat[dim]
            if v > 0:
                data[key][dim].append(v)

    rows = []
    for key, dims in data.items():
        agent, location, month = key
        total = calls_count[key]
        out   = outcomes[key]
        converted = out.count("Converted")
        store_visits = out.count("Store Visit")

        row = {
            "agent_name":     agent,
            "location":       location,
            "month":          month,
            "total_calls":    total,
            "converted":      converted,
            "conversion_rate": round(converted / total * 100, 1) if total else 0,
            "store_visits":   store_visits,
        }
        for dim, vals in dims.items():
            row[f"avg_{dim}"] = round(sum(vals) / len(vals), 2) if vals else 0.0

        rows.append(row)

    rows.sort(key=lambda x: (x["month"], x["agent_name"]))

    if not rows:
        return 0

    path = os.path.join(EXPORT_DIR, "agent_scorecard.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    print(f"Loading analysis files from {ANALYSIS_DIR} ...")

    records = load_all_analyses()
    print(f"Loaded {len(records)} records\n")

    n_calls = export_calls(records)
    print(f"  calls.csv            : {n_calls} rows")

    n_voice = export_customer_voice(records)
    print(f"  customer_voice.csv   : {n_voice} rows")

    n_coach = export_coaching(records)
    print(f"  coaching.csv         : {n_coach} rows")

    n_score = export_agent_scorecard(records)
    print(f"  agent_scorecard.csv  : {n_score} rows (agent × month)")

    print(f"\nExports saved to: {EXPORT_DIR}/")
    print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
