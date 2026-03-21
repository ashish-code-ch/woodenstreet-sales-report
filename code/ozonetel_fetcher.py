#!/usr/bin/env python3
"""
Ozonetel Daily Call Fetcher
===========================
Fetches all call metadata for a given date, downloads MP3s for calls
with talktime >= MIN_TALK_SEC, and produces per-agent stats.

Usage:
  python3 ozonetel_fetcher.py                  # yesterday
  python3 ozonetel_fetcher.py --date 2026-03-19
  python3 ozonetel_fetcher.py --date 2026-03-19 --no-download
  python3 ozonetel_fetcher.py --stats-only
"""

import urllib.request, urllib.error
import json, base64, csv, os, ast, time, argparse, sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from agents import resolve_agent_by_fullname

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
ARCHIVE_DIR  = BASE_DIR / "ozonetel_archive"
CREDS_FILE   = BASE_DIR / "ozonetel_creds.json"
BASE_API     = "https://api.cloudagent.ozonetel.com"
REPORTS_URL  = f"{BASE_API}/reportApi/endpoint/reports"
MIN_TALK_SEC = 20          # only download MP3 if talktime >= this
PAGE_SIZE    = 100         # records per API page
MAX_WORKERS  = 4           # parallel MP3 downloads

# Default credentials (can be overridden by ozonetel_creds.json)
DEFAULT_USER = "qc_all"
DEFAULT_PASS = "WS_qc321"


# ── Auth ─────────────────────────────────────────────────────────────────────
def get_token():
    if CREDS_FILE.exists():
        creds = json.loads(CREDS_FILE.read_text())
        user, pwd = creds["username"], creds["password"]
    else:
        user, pwd = DEFAULT_USER, DEFAULT_PASS

    data = json.dumps({"username": user, "password": pwd}).encode()
    req  = urllib.request.Request(f"{BASE_API}/auth/login", data=data,
             headers={"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    token = resp["token"]
    pl    = token.split(".")[1]; pl += "=" * (4 - len(pl) % 4)
    uid   = str(json.loads(base64.b64decode(pl))["userId"])
    return token, uid


def make_headers(token, uid):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "userId":        uid,
        "dAccessType":   "false",
        "userName":      DEFAULT_USER,
        "Accept":        "application/json",
        "User-Agent":    "Mozilla/5.0",
    }


# ── API helpers ──────────────────────────────────────────────────────────────
def api_post(url, payload, headers, timeout=20):
    req  = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode())


def fetch_all_calls(date_str, hdrs, verbose=True):
    """Fetch every call record for date_str (YYYY-MM-DD). Returns list of dicts."""
    base_payload = {
        "fromTime": f"{date_str} 00:00:00",
        "toTime":   f"{date_str} 23:59:59",
        "campaignName":"All","phoneName":"All","skill":"All","agent":"All",
        "location":"All","callID":"","UCID":"","fallbackRule":"","type":"",
        "callStatus":"All","transfered":"All","callType":"All","callEvent":"All",
        "disposition":"All","reportType":"","durationinsecs":"All","callerNo":"",
        "durationInput":"","sortOrderColumn":"","sortOrderType":0,
        "sortAsString":False,"requestExcecutionId":None,"totalNoOfRows":0,
        "uui":"","filterValue":1,"group":"All",
        "pageNo":1,"rowsPerPage":PAGE_SIZE,
    }

    # Get total count first
    first = api_post(f"{REPORTS_URL}/callDetailsV2Report", base_payload, hdrs)
    total      = first["totalNoOfRows"]
    total_pages = first["totalNoPages"]
    all_records = first["reports"]

    if verbose:
        print(f"  📋 Total calls on {date_str}: {total:,} ({total_pages} pages)")

    for page in range(2, total_pages + 1):
        if verbose and page % 5 == 0:
            print(f"  Fetching page {page}/{total_pages}...")
        payload = {**base_payload, "pageNo": page,
                   "requestExcecutionId": None}
        try:
            resp = api_post(f"{REPORTS_URL}/callDetailsV2Report", payload, hdrs)
            all_records.extend(resp["reports"])
        except Exception as e:
            print(f"  ⚠ Page {page} failed: {e}")
        time.sleep(0.05)   # gentle rate limiting

    if verbose:
        print(f"  ✓ Fetched {len(all_records):,} records")
    return all_records


# ── Talk time parser ──────────────────────────────────────────────────────────
def hms_to_sec(s):
    """Convert HH:MM:SS string to integer seconds."""
    if not s or s in ("None", ""):
        return 0
    try:
        parts = s.split(":")
        return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
    except:
        return 0


# ── Campaign → team classifier ────────────────────────────────────────────────
def classify_team_from_record(r):
    """
    Determine BD Sales vs Customer Support from campaign/skill fields.
    More reliable than name matching when skillName or campaignName is set.
    """
    campaign = (r.get("campaignName") or "").lower()
    skill    = (r.get("skillName") or "").lower()

    if "outbound_sales" in campaign or "inbound_sales" in skill:
        return "BD Sales"
    if "outbound_support" in campaign or "inbound_support" in skill or "inbound_scm" in skill:
        return "Customer Support"
    return None   # let agent lookup decide


# ── Agent stats ───────────────────────────────────────────────────────────────
def compute_stats(records):
    """
    Return per-agent dict with full activity metrics.

    Primary key  : agentID (W-number) if present, else agentConnected name.
    Cross-check  : stats are computed by BOTH agentID and name and compared.
                   If they differ, the mismatch is flagged in the output.

    Rich metrics : talkTime, handlingTime, wrapupTime, holdTime, queueTime,
                   disposition breakdown, campaign-based team classification,
                   agent_disconnect count, user_disconnect count.
    """
    # Primary accumulator — keyed by agentID if present, else agentConnected name
    agents = defaultdict(lambda: {
        "total_calls": 0,
        "answered": 0,
        "unanswered": 0,
        "dropped": 0,           # answered but talktime < 5s (immediate hangup)
        "agent_disconnected": 0,# agent hung up during call (eventStatusFlow = agent_disconnect)
        "talk_seconds": 0,
        "handling_seconds": 0,  # talk + hold + wrapup
        "wrapup_seconds": 0,
        "hold_seconds": 0,
        "queue_seconds": 0,     # customer wait time in queue
        "recordings_available": 0,
        "locations": set(),
        "teams_seen": set(),
        "dispositions": defaultdict(int),
        "inbound_calls": 0,
        "outbound_calls": 0,
        "names_seen": set(),    # agentConnected name variants seen for this ID
        "agent_id": "",
    })

    # Cross-check accumulator keyed by agentConnected name only (for comparison)
    name_totals = defaultdict(lambda: {"talk_seconds": 0, "answered": 0, "total": 0})

    for r in records:
        agent_name = (r.get("agentConnected") or "").strip() or "Unknown"
        agent_id   = (r.get("agentID") or "").strip()

        # Canonical key: prefer ID (stable) over name (can have typos/spaces)
        key = agent_id if agent_id else agent_name

        status       = r.get("status", "").lower()
        talk_sec     = hms_to_sec(r.get("talkTime", ""))
        handling_sec = hms_to_sec(r.get("handlingTime", ""))
        wrapup_sec   = hms_to_sec(r.get("wrapupTime", ""))
        hold_sec     = hms_to_sec(r.get("holdTime", ""))
        queue_sec    = hms_to_sec(r.get("queueTime", ""))
        loc          = r.get("locationName", "") or ""
        call_type    = r.get("callType", "")
        event_flow   = r.get("eventStatusFlow", "").lower()
        disposition  = (r.get("disposition") or "").strip()

        # Determine team from campaign/skill (most reliable signal)
        team_from_campaign = classify_team_from_record(r)

        a = agents[key]
        a["total_calls"]       += 1
        a["locations"].add(loc)
        a["names_seen"].add(agent_name)
        a["agent_id"] = agent_id
        if team_from_campaign:
            a["teams_seen"].add(team_from_campaign)
        if call_type == "Inbound":
            a["inbound_calls"]  += 1
        else:
            a["outbound_calls"] += 1
        if disposition and disposition not in ("None", "null", ""):
            # Strip transfer chains from disposition keys too
            disp_key = disposition.split(" -> ")[0].strip()
            if disp_key:
                a["dispositions"][disp_key] += 1

        if status == "answered":
            a["answered"]          += 1
            a["talk_seconds"]      += talk_sec
            a["handling_seconds"]  += handling_sec
            a["wrapup_seconds"]    += wrapup_sec
            a["hold_seconds"]      += hold_sec
            a["queue_seconds"]     += queue_sec
            if talk_sec < 5:
                a["dropped"]       += 1   # picked up but immediately hung up
            if "agent_disconnect" in event_flow:
                a["agent_disconnected"] += 1
        else:
            a["unanswered"] += 1

        audio = r.get("callAudioURL", "")
        if audio and audio not in ("None", "null", ""):
            a["recordings_available"] += 1

        # Cross-check accumulator
        n = name_totals[agent_name]
        n["total"] += 1
        if status == "answered":
            n["answered"]     += 1
            n["talk_seconds"] += talk_sec

    # Build result dict
    result = {}

    for key, d in sorted(agents.items(), key=lambda x: -x[1]["total_calls"]):
        # Skip transfer-chain name entries (Ozonetel artefacts)
        if " -> " in key:
            continue

        talk_sec     = d["talk_seconds"]
        talk_hrs     = round(talk_sec / 3600, 2)
        handling_hrs = round(d["handling_seconds"] / 3600, 2)
        lost         = d["unanswered"] + d["dropped"]

        # Determine display name: use the most common agentConnected name seen
        from agents import resolve_agent_by_fullname, SUPPORT_AGENTS
        names = d["names_seen"] - {"Unknown", ""}
        full_name = max(names, key=lambda n: 1) if names else key  # pick first non-empty name

        # Resolve team/location: try agentID in SUPPORT_AGENTS first, then name lookup
        agent_id_val = d["agent_id"]
        if agent_id_val in SUPPORT_AGENTS:
            info = SUPPORT_AGENTS[agent_id_val]
        else:
            info = resolve_agent_by_fullname(full_name)

        # Team: prefer campaign-derived, then agents.py, then Unknown
        team_candidates = list(d["teams_seen"])
        if team_candidates:
            # If all calls agree on team, use that; if mixed, pick majority
            team = max(set(team_candidates), key=team_candidates.count)
        else:
            team = info.get("team", "Unknown")

        # Cross-check: compare ID-keyed talk time vs name-keyed talk time
        # If they match (within 60s rounding), we're confident the data is consistent
        name_data    = name_totals.get(full_name, {})
        xcheck_talk  = name_data.get("talk_seconds", 0)
        talk_matches = abs(talk_sec - xcheck_talk) <= 60   # allow 1-min rounding

        # Top dispositions (max 5)
        top_dispositions = dict(
            sorted(d["dispositions"].items(), key=lambda x: -x[1])[:5]
        )

        result[full_name] = {
            "agent_id":             agent_id_val,
            "total_calls":          d["total_calls"],
            "answered":             d["answered"],
            "unanswered":           d["unanswered"],
            "dropped":              d["dropped"],
            "agent_disconnected":   d["agent_disconnected"],
            "lost_calls":           lost,
            "inbound_calls":        d["inbound_calls"],
            "outbound_calls":       d["outbound_calls"],
            "talk_seconds":         talk_sec,
            "talk_hours":           talk_hrs,
            "handling_seconds":     d["handling_seconds"],
            "handling_hours":       handling_hrs,
            "wrapup_seconds":       d["wrapup_seconds"],
            "hold_seconds":         d["hold_seconds"],
            "queue_seconds":        d["queue_seconds"],
            "recordings_available": d["recordings_available"],
            "locations":            ", ".join(sorted(d["locations"] - {""})) or info.get("location", "—"),
            "team":                 team,
            "avg_talk_sec":         round(talk_sec / d["answered"]) if d["answered"] else 0,
            "drop_rate_pct":        round(d["dropped"] / d["answered"] * 100, 1) if d["answered"] else 0,
            "unanswered_rate_pct":  round(d["unanswered"] / d["total_calls"] * 100, 1),
            "lost_rate_pct":        round(lost / d["total_calls"] * 100, 1),
            "below_3h_target":      team == "BD Sales" and talk_hrs < 3.0,
            "shortfall_hours":      round(3.0 - talk_hrs, 2) if (team == "BD Sales" and talk_hrs < 3.0) else 0,
            "top_dispositions":     top_dispositions,
            # Cross-check result
            "xcheck_name_talk_sec": xcheck_talk,
            "xcheck_match":         talk_matches,
        }

    return result


# ── MP3 downloader ────────────────────────────────────────────────────────────
def parse_audio_url(raw):
    """Extract first URL from field — handles list object, list string, or plain string."""
    if not raw or raw in ("None","null",""):
        return None
    # Already a Python list (from fresh API response)
    if isinstance(raw, list):
        return raw[0] if raw else None
    # String representation of a list like "['https://...']"
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                urls = ast.literal_eval(raw)
                return urls[0] if urls else None
            except:
                # Fallback: strip brackets and quotes manually
                inner = raw.strip("[]").strip("'\"")
                return inner if inner.startswith("http") else None
        return raw if raw.startswith("http") else None
    return None


def download_mp3(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if len(data) < 1000:   # reject tiny/empty files
        return 0
    with open(dest_path, "wb") as f:
        f.write(data)
    return len(data)


def download_recordings(records, rec_dir, min_talk_sec=MIN_TALK_SEC, verbose=True):
    """Download MP3s for calls with talktime >= min_talk_sec. Returns stats dict."""
    eligible = []
    for r in records:
        url = parse_audio_url(r.get("callAudioURL",""))
        if url and hms_to_sec(r.get("talkTime","")) >= min_talk_sec:
            eligible.append((r, url))

    if verbose:
        print(f"\n  🎵 Eligible for download (talktime ≥ {min_talk_sec}s): {len(eligible)}")

    rec_dir.mkdir(parents=True, exist_ok=True)
    downloaded, skipped, failed = 0, 0, 0

    for i, (r, url) in enumerate(eligible, 1):
        agent   = (r.get("agentConnected") or "unknown").replace(" ","_").replace("/","_")
        date_   = r.get("callDate","").replace("-","")
        start   = r.get("startTime","").replace(":","")
        fname   = f"{date_}__{agent}__{start}__{r.get('callID','')}.mp3"
        fpath   = rec_dir / fname

        if fpath.exists():
            skipped += 1
            continue

        try:
            size = download_mp3(url, fpath)
            if size:
                downloaded += 1
                if verbose:
                    print(f"  [{i}/{len(eligible)}] ✓ {fname}  ({size//1024} KB)")
            else:
                failed += 1
        except Exception as e:
            failed += 1
            if verbose:
                print(f"  [{i}/{len(eligible)}] ✗ {fname}: {e}")
        time.sleep(0.03)

    return {"downloaded": downloaded, "skipped": skipped, "failed": failed, "total_eligible": len(eligible)}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Ozonetel daily call fetcher")
    parser.add_argument("--date",         default=None, help="YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--no-download",  action="store_true", help="Skip MP3 downloads")
    parser.add_argument("--stats-only",   action="store_true", help="Print stats from saved metadata")
    parser.add_argument("--min-talk",     type=int, default=MIN_TALK_SEC, help=f"Min talktime seconds for download (default {MIN_TALK_SEC})")
    args = parser.parse_args()

    target_date = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    day_dir     = ARCHIVE_DIR / target_date
    meta_csv    = day_dir / "metadata.csv"
    stats_json  = day_dir / "stats.json"
    rec_dir     = day_dir / "recordings"

    print(f"\n{'='*60}")
    print(f"  Ozonetel Fetcher — {target_date}")
    print(f"{'='*60}")

    # ── Stats-only mode ──
    if args.stats_only:
        if not stats_json.exists():
            print(f"  ✗ No stats file found at {stats_json}")
            sys.exit(1)
        stats = json.loads(stats_json.read_text())
        print_stats(stats, target_date)
        return

    # ── Fetch metadata ──
    if meta_csv.exists():
        print(f"\n  ℹ Metadata already exists ({meta_csv}), loading from cache...")
        with open(meta_csv) as f:
            records = list(csv.DictReader(f))
        print(f"  ✓ Loaded {len(records):,} records from cache")
    else:
        print(f"\n  🔑 Logging in to Ozonetel...")
        token, uid = get_token()
        hdrs = make_headers(token, uid)

        print(f"\n  📡 Fetching call metadata for {target_date}...")
        records = fetch_all_calls(target_date, hdrs)

        # Save metadata CSV
        day_dir.mkdir(parents=True, exist_ok=True)
        if records:
            fieldnames = list(records[0].keys())
            with open(meta_csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(records)
            print(f"  💾 Metadata saved → {meta_csv}")

    # ── Compute stats ──
    print(f"\n  📊 Computing per-agent stats...")
    stats = compute_stats(records)

    # Save stats
    stats_serializable = {k: {**v, "locations": v["locations"] if isinstance(v["locations"],str) else ", ".join(sorted(v["locations"]))} for k,v in stats.items()}
    with open(stats_json, "w") as f:
        json.dump(stats_serializable, f, indent=2)

    print_stats(stats, target_date)

    # ── Download recordings ──
    if not args.no_download:
        print(f"\n  ⬇  Downloading recordings (talktime ≥ {args.min_talk}s)...")
        dl_stats = download_recordings(records, rec_dir, min_talk_sec=args.min_talk)
        print(f"\n  ✅ Downloads complete:")
        print(f"     Downloaded : {dl_stats['downloaded']}")
        print(f"     Skipped    : {dl_stats['skipped']} (already existed)")
        print(f"     Failed     : {dl_stats['failed']}")
        print(f"     Total MP3s : {dl_stats['total_eligible']}")
    else:
        print(f"\n  ⏭  Skipping downloads (--no-download)")

    print(f"\n  📁 Archive: {day_dir}")
    print(f"  📋 Metadata: {meta_csv.name}  ({len(records):,} rows)")
    if not args.no_download:
        mp3s = list(rec_dir.glob("*.mp3")) if rec_dir.exists() else []
        print(f"  🎵 Recordings: {len(mp3s)} files in recordings/")
    print()


def print_stats(stats, date_str):
    total_calls = sum(v["total_calls"] for v in stats.values())
    total_talk  = sum(v["talk_seconds"] for v in stats.values())
    total_drop  = sum(v["dropped"] for v in stats.values())
    total_unans = sum(v["unanswered"] for v in stats.values())

    print(f"\n  ┌── Team Summary for {date_str} {'─'*30}")
    print(f"  │  Total calls     : {total_calls:,}")
    print(f"  │  Total talk time : {total_talk//3600}h {(total_talk%3600)//60}m")
    print(f"  │  Total dropped   : {total_drop}  (answered < 5s)")
    print(f"  │  Total unanswered: {total_unans}")
    print(f"  └{'─'*50}")

    # Table header
    print(f"\n  {'Agent':<25} {'City':<14} {'Total':>5} {'Ans':>5} {'Unans':>5} {'Drop':>5} {'Drop%':>6} {'TalkMin':>8} {'AvgCall':>8}")
    print(f"  {'─'*25} {'─'*14} {'─'*5} {'─'*5} {'─'*5} {'─'*5} {'─'*6} {'─'*8} {'─'*8}")

    for agent, v in list(stats.items())[:50]:   # top 50 by call volume
        city = v["locations"][:13]
        talk_min = v["talk_seconds"] // 60
        avg_sec  = v["avg_talk_sec"]
        drop_flag = " ⚠" if v["drop_rate_pct"] >= 20 else ""
        print(f"  {agent:<25} {city:<14} {v['total_calls']:>5} {v['answered']:>5} "
              f"{v['unanswered']:>5} {v['dropped']:>5} {v['drop_rate_pct']:>5.1f}% "
              f"{talk_min:>7}m {avg_sec:>7}s{drop_flag}")


if __name__ == "__main__":
    main()
