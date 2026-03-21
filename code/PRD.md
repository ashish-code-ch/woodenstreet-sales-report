# Product Requirements Document
## WoodenStreet Sales Scorecard — AI-Powered Call Intelligence Platform

**Version:** 1.3
**Date:** 2026-03-21
**Owner:** Ashish
**Status:** Active — Implementation In Progress

---

## 1. Executive Summary

WoodenStreet Sales Scorecard is an end-to-end call intelligence platform that automatically ingests, transcribes, scores, and visualizes sales calls made by WoodenStreet's Business Development (BD) and Customer Support (CS) teams. The system uses AI (Whisper for transcription, Claude Haiku for analysis) to produce agent performance scorecards, customer voice data, and sales pattern insights — delivered daily to Power BI dashboards and a live HTML report.

**Primary Users:** Sales Manager, Team Leads, HR/Training
**Secondary Users:** Individual Agents (coaching feedback), Data Analytics team
**Business Goal:** Improve conversion rates, reduce churn risk, accelerate agent coaching at scale

---

## 2. Problem Statement

### 2.1 Current Challenges
- **No systematic call review:** Managers cannot manually review hundreds of daily calls (1,000–1,500/day)
- **Subjective coaching:** Feedback is ad hoc, inconsistent across locations (JPR, UDR, BLR)
- **Invisible customer signals:** What customers actually ask for, complain about, or need is buried in audio files
- **Delayed insights:** Performance data is available days or weeks late, not the next morning
- **Language barrier:** Most calls are in Hinglish (Hindi + English mix) making manual review harder

### 2.2 Opportunity
If every call is scored overnight, managers can focus their morning on the 20% of calls that need attention — escalations, lost conversions, and agents below threshold — instead of listening to all calls.

---

## 3. Goals & Success Metrics

| Goal | Metric | Target |
|------|--------|--------|
| Full call coverage | % of eligible calls analyzed | ≥ 95% |
| Turnaround time | Insights available by | 9:00 AM daily |
| Analysis accuracy | Human spot-check agreement | ≥ 80% |
| Cost efficiency | Cost per call analyzed | < ₹0.30/call (~$0.0036) |
| Agent improvement | Avg score change over 30 days | +10% for coached agents |

---

## 4. Product Scope

### 4.1 In Scope
- Automated daily ingestion of call recordings from Ozonetel PBX
- Speech-to-text transcription with speaker identification (agent vs customer)
- Separate AI analysis prompts for BD Sales and Customer Support teams
- Per-call AI analysis: scoring, intent detection, sentiment, escalation flags
- Team-level pattern synthesis: winning/losing behaviors, top objections
- Export to 5 structured CSVs across monthly, quarterly, and master tiers
- Live HTML dashboard published to GitHub Pages (auto-pushed after every pipeline run)
- Team-switched HTML report: BD Sales and Customer Support views via URL param
- Operations dashboard: Ozonetel-powered hourly patterns, queue drops, per-agent ops
- Call recording drill-through: inline audio player linked from call detail view
- Daily activity tracking: talk time vs 3-hour target, lost calls per agent

### 4.2 Out of Scope (v1)
- Real-time / live call analysis
- WhatsApp or chat channel analysis
- Agent self-service portal
- Automated coaching messages to agents
- CRM write-back (Salesforce, HubSpot, etc.)

---

## 5. User Personas

### Persona 1 — Sales Manager (Primary)
- Reviews team performance every morning
- Wants to know: who is underperforming, what objections are trending, which calls need follow-up
- Does not want to listen to recordings; wants text summaries and scores

### Persona 2 — Team Lead / Floor Supervisor
- Coaches individual agents based on call feedback
- Needs specific, actionable coaching tips per agent per call
- Reviews agent monthly and quarterly trends to track improvement

### Persona 3 — HR / Training Team
- Uses aggregated data to design training content
- Needs: top gaps across team, common objections agents fail at, best-practice call examples

### Persona 4 — Data Analyst
- Refreshes Power BI from exported CSVs in `exports/master/`
- Needs: stable, well-structured CSV schemas with consistent column names

---

## 6. System Architecture

### 6.1 High-Level Data Flow

```
Ozonetel PBX (call recordings + metadata)
        │
        ▼
[Stage 0] ozonetel_fetcher.py  (--date YYYY-MM-DD)
  - Authenticate to Ozonetel API (callDetailsV2Report, 48 columns)
  - Paginate through all call records for target date
  - Download MP3s for calls with talktime ≥ 20s
  - Save metadata.csv + stats.json to ozonetel_archive/{date}/
  - Compute per-agent stats: talk time, lost calls, 3h target gap
  - Symlink MP3s → recording/{date}/
        │
        ▼
[Stage 1] transcribe_diarize.py  (--date YYYY-MM-DD)
  - Read audio from recording/{date}/
  - Transcribe with faster-whisper large-v3 (GPU, float16)
  - Diarize speakers with pyannote/speaker-diarization-3.1
  - Align words → speakers, fix fragmentation, remove hallucinations
  - Output: *_diarized.json + *_diarized.txt → transcripts/{date}/
  - Move processed audio to recording/{date}/done/
  - Skip already-transcribed files (idempotent resume)
        │
        ▼
[Stage 2] analyze_calls.py  (--batch --from-date YYYY-MM-DD)
  - Skip transcripts < 150 words (noise/dropped/wrong number)
  - Route to BD or CS prompt based on agent team
  - Submit all pending calls as single Anthropic Batch API request
  - Poll until complete (30s intervals), save results as they land
  - Output: *_analysis.json → analysis/{date}/
  - Fallback: real-time mode (--workers N) for immediate results
        │
        ▼
[Stage 3] export_to_csv.py
  - Read from analysis/{date}/ (all dates)
  - Generate three export tiers:
      exports/{YYYY-MM}/      ← monthly CSVs
      exports/{YYYY-QN}/      ← quarterly CSVs
      exports/master/         ← all-time union (Power BI connects here)
  - Output per tier: calls.csv, customer_voice.csv, coaching.csv,
    agent_scorecard.csv + daily_activity.csv (master only)
        │
        ▼
[Stage 3b] generate_report.py  (--date YYYY-MM-DD)
  - Read all analysis/{date}/*_analysis.json files
  - Split records by team (BD Sales / Customer Support)
  - Load Ozonetel metadata.csv for Operations metrics
  - Build JS data objects: BD, CS, BD_ACT, CS_ACT, BD_CALLS, CS_CALLS, OPS
  - Generate standalone index.html with team switcher (?team=bd / ?team=cs)
  - Output: index.html (project root)
        │
        ▼
[Stage 4] push to GitHub Pages
  - Read token from github_token.txt
  - Clone or pull woodenstreet-sales-report repo to /tmp/ws-report
  - Copy index.html → repo root
  - Copy source files → repo/code/ subfolder
  - git commit + push
  - Auto-deployed to https://ashish-code-ch.github.io/woodenstreet-sales-report/
```

### 6.2 Orchestrator — pipeline.py

The `pipeline.py` script is the single entry point for daily execution:
- Runs Stage 0 → 1 → 2 → 3 → 4 sequentially with conditional skips
- Stage 0 (fetch) defaults to yesterday; `--date` overrides
- Stage 2 (analyse) passes `--batch --from-date` automatically
- Stage 3 (export) runs `export_to_csv.py` then `generate_report.py`
- Stage 4 (push) is **automatic and mandatory** — always runs after export
- Persists execution history to `pipeline_state.json` (last 30 runs)
- Logs all output to timestamped files in `/logs/`
- Returns exit code 0 (success) or 1 (any stage failed)

**Run commands:**
```bash
python pipeline.py                              # Full pipeline (yesterday)
python pipeline.py --date 2026-03-19            # Full pipeline for specific date
python pipeline.py --stage fetch --date 2026-03-19  # Only fetch
python pipeline.py --stage transcribe           # Only transcription
python pipeline.py --stage analyse              # Only analysis (batch)
python pipeline.py --stage export               # Only CSV export + HTML + push
python pipeline.py --stage push                 # Only push to GitHub Pages
python pipeline.py --status                     # Show last run status
```

---

## 7. Feature Requirements

### 7.1 Call Ingestion (ozonetel_fetcher.py)

| ID | Requirement | Status |
|----|-------------|--------|
| F-01 | Fetch all call records for a given date from Ozonetel API (paginated, 100 records/page) | ✓ Done |
| F-02 | Download MP3 recordings for calls with talktime ≥ 20 seconds | ✓ Done |
| F-03 | Save per-call metadata to `metadata.csv` (call ID, agent, timestamp, duration, phone, disposition) | ✓ Done |
| F-04 | Compute per-agent statistics: total calls, answered, unanswered, dropped, talk time, lost calls | ✓ Done |
| F-05 | Support custom date input (default: yesterday) | ✓ Done |
| F-06 | Archive recordings: `ozonetel_archive/{YYYY-MM-DD}/recordings/*.mp3` | ✓ Done |
| F-07 | Skip already-downloaded recordings (idempotent) | ✓ Done |
| F-08 | Completeness check: compare expected vs downloaded, retry once if gap detected | ✓ Done |
| F-09 | Symlink new recordings into `recording/{YYYY-MM-DD}/` for Stage 1 | ✓ Done |
| F-10 | Classify team per call using campaignName/skillName (BD Sales vs Customer Support) | ✓ Done |
| F-11 | Track BD agent talk time vs 3-hour daily target; flag shortfall | ✓ Done |
| F-12 | Enrich stats with all 48 Ozonetel columns (talkTime, handlingTime, disposition, etc.) | ✓ Done |

### 7.2 Transcription (transcribe_diarize.py)

| ID | Requirement | Status |
|----|-------------|--------|
| T-01 | Transcribe audio using faster-whisper large-v3 (GPU, float16) | ✓ Done |
| T-02 | Always output in English (translate mode handles Hinglish) | ✓ Done |
| T-03 | Identify 2 speakers: SPEAKER_00 (agent), SPEAKER_01 (customer) | ✓ Done |
| T-04 | Generate per-word timestamps and confidence scores | ✓ Done |
| T-05 | Remove hallucinated segments (VAD gating, compression ratio filter) | ✓ Done |
| T-06 | Fix diarization fragmentation (3-pass merge for micro-turns) | ✓ Done |
| T-07 | Output `*_diarized.json` + `*_diarized.txt` to `transcripts/{YYYY-MM-DD}/` | ✓ Done |
| T-08 | Accept `--date YYYY-MM-DD` to route input/output to dated folders | ✓ Done |
| T-09 | Skip already-transcribed files (idempotent — safe to restart after crash) | ✓ Done |
| T-10 | Move processed audio to `recording/{date}/done/` after each file | ✓ Done |

### 7.3 Call Analysis (analyze_calls.py)

| ID | Requirement | Status |
|----|-------------|--------|
| A-01 | Skip transcripts with fewer than 150 words (noise/dropped/wrong number) | ✓ Done |
| A-02 | Route to BD Sales prompt or Customer Support prompt based on agent team | ✓ Done |
| A-03 | Stamp `prompt_version` (bd_v1 / cs_v1) in every output JSON | ✓ Done |
| A-04 | Detect primary call intent per team type | ✓ Done |
| A-05 | Score agent on 7 dimensions (1–10 scale) per call | ✓ Done |
| A-06 | Detect customer sentiment and arc | ✓ Done |
| A-07 | Flag escalation risk with specific triggers | ✓ Done |
| A-08 | Extract customer voice: top asks, issues raised, product/service gaps | ✓ Done |
| A-09 | Identify call outcome with resolution type | ✓ Done |
| A-10 | Flag compliance red flags: wrong policy info, unauthorized promises | ✓ Done |
| A-11 | **Batch API mode** (default): submit all calls in one request, 50% cheaper | ✓ Done |
| A-12 | Real-time fallback mode with parallel workers (--workers N) | ✓ Done |
| A-13 | Retry on API error: 3 attempts with exponential backoff (2s, 4s, 8s) | ✓ Done |
| A-14 | Resume interrupted batch via `--batch-resume BATCH_ID` | ✓ Done |
| A-15 | Save analysis JSONs to `analysis/{YYYY-MM-DD}/` dated subfolders | ✓ Done |
| A-16 | Skip already-analyzed files (idempotent) | ✓ Done |
| A-17 | Filter by `--from-date YYYY-MM-DD` to skip legacy files | ✓ Done |
| A-18 | Aggregate per-agent scorecards + team-level pattern synthesis | ✓ Done |

#### 7.3.1 BD Sales Scoring Dimensions (system_prompt_bd.py — bd_v1)

| Dimension | Weight |
|-----------|--------|
| Opening Greeting | 1.0x |
| Needs Discovery | 1.5x |
| Product Knowledge | 1.0x |
| Objection Handling | 1.0x |
| Closing Attempt | 1.5x |
| Empathy & Tone | 1.0x |
| Communication Clarity | 1.0x |

**Overall Score** = Weighted average / 10

#### 7.3.2 Customer Support Scoring Dimensions (system_prompt_cs.py — cs_v1)

| Dimension | Weight |
|-----------|--------|
| Empathy & Listening | 2.0x |
| Problem Understanding | 1.5x |
| Resolution Quality | 1.5x |
| Follow-Through | 1.5x |
| Policy Knowledge | 1.0x |
| Escalation Handling | 1.0x |
| Communication Clarity | 1.0x |

**Overall Score** = Weighted average / 10.5

### 7.4 Data Export (export_to_csv.py)

| ID | Requirement | Status |
|----|-------------|--------|
| E-01 | Export `calls.csv` — one row per call with all metadata, scores, outcomes | ✓ Done |
| E-02 | Export `customer_voice.csv` — one row per ask/issue/gap/feedback item | ✓ Done |
| E-03 | Export `coaching.csv` — one row per coaching note | ✓ Done |
| E-04 | Export `agent_scorecard.csv` — aggregated per agent × period | ✓ Done |
| E-05 | Export `daily_activity.csv` — one row per agent per date (all calls incl. lost) | ✓ Done |
| E-06 | Generate three export tiers: monthly, quarterly, master | ✓ Done |
| E-07 | Add `quarter` column to all CSVs (e.g. 2026-Q1) | ✓ Done |
| E-08 | `exports/master/` is the stable endpoint for Power BI and GitHub Pages | ✓ Done |
| E-09 | Handle both legacy (flat) and new (dated subfolder) analysis JSONs | ✓ Done |

#### 7.4.1 Export Folder Structure

```
exports/
  2026-03/              ← March 2026 monthly CSVs
  2026-Q1/              ← Q1 2026 (Jan+Feb+Mar) quarterly CSVs
  master/               ← All-time union ← Power BI connects here
    calls.csv
    customer_voice.csv
    coaching.csv
    agent_scorecard.csv
    daily_activity.csv
    report.html
```

#### 7.4.2 calls.csv Key Columns

```
call_id, timestamp, date, month, quarter, agent_name, location, team,
duration_sec, total_words, num_turns,
primary_intent, sub_intent, urgency_level,
competitor_mentioned, upsell_signal, competitor_switch,
sentiment_overall, sentiment_score, opening_emotion, closing_emotion, churn_risk,
score_opening, score_discovery, score_product, score_objection,
score_closing, score_empathy, score_clarity, score_overall,
resolution_type, resolved, follow_up_required,
agent_talk_pct, customer_talk_pct,
unauthorized_promise, wrong_policy_info,
call_summary
```

### 7.5 Reporting & Dashboard

| ID | Requirement | Status |
|----|-------------|--------|
| R-01 | Generate standalone `index.html` with all insights (no external dependencies) | ✓ Done |
| R-02 | Publish report to GitHub Pages after each pipeline run (Stage 4 — automatic) | ✓ Done |
| R-03 | Power BI connects to `exports/master/` CSVs as daily-refreshed data source | ✓ Done |
| R-04 | Report team switcher: BD Sales vs Customer Support via `?team=bd` / `?team=cs` | ✓ Done |
| R-05 | Operations Dashboard: Ozonetel-powered ops metrics (no Claude cost) | ✓ Done |
| R-06 | Call recording drill-through: inline HTML5 audio player per call in Call Detail tab | ✓ Done |

#### 7.5.1 HTML Report Tabs

| Tab | Team | Contents |
|-----|------|----------|
| Overview | BD / CS | KPI cards, score distribution, sentiment, churn risk |
| Dimensions | BD / CS | 7-dimension radar/bar charts, weakest dimensions |
| Leaderboard | BD / CS | Agent rankings by overall score, top 5 highlights |
| Heatmap | BD / CS | Agent × dimension grid with colour coding |
| Activity | BD / CS | Daily call volume, outcomes, intents over time |
| Call Detail | BD / CS | Per-call rows with expand → summary, scores, audio player |
| Coaching | BD / CS | Per-agent coaching notes, improvement areas |
| Operations | Both | Ozonetel metrics: queue drops, outbound no-pickup, hourly charts, per-agent ops |

#### 7.5.2 Operations Dashboard (R-05)

Sources `ozonetel_archive/{date}/metadata.csv` directly — no Claude analysis needed.

**KPI metrics:**
- Total / answered / unanswered call counts; answer rate
- Avg hold time, wrapup time, time-to-answer
- Agent-initiated hangup count and rate

**Charts:**
- Inbound queue drops by hour (customers who called and gave up before agent picked up)
- Outbound no-pickup by hour (agent dialled, customer didn't answer)
- Stacked hourly chart: answered vs unanswered vs total
- Unanswered reasons donut
- Dispositions horizontal bar
- Disconnect type donut

**Per-agent ops table:**
- Daily avg talk time (minutes) with progress bar
- Flag: `⚠ Low Talk` if daily avg < 180 min (3-hour target)
- Flag: `Slow Wrapup` if avg wrapup > 120s
- Flag: `High Hangups` if agent-initiated hangup rate > 30%
- Flag: `Low Contact` if contact rate < 40% (outbound agents)

---

## 8. Non-Functional Requirements

### 8.1 Performance
- Transcription: ~40s per call on RTX 3060 (12GB VRAM); 1,000 calls ≈ 11 hours
- Analysis (Batch API): ~900 calls submitted in one batch; results in minutes to hours
- Analysis (real-time fallback): 5 parallel workers ≈ 18 minutes for 900 calls
- Full pipeline for 1,000 calls: transcription overnight, analysis + export by 7 AM

### 8.2 Cost (Optimised)

| Item | Monthly Cost |
|------|-------------|
| Whisper transcription (~1,000 calls/day) | $0 — local GPU |
| pyannote diarization | $0 — local GPU |
| **Claude Haiku 3 — Batch API** | **~$34/month** |
| GitHub Pages report hosting | $0 |
| Ozonetel API | Existing contract |
| **Total AI infrastructure** | **~$34/month** |

**Cost optimisations applied:**

| Optimisation | Saving | Detail |
|---|---|---|
| Anthropic Batch API | 50% off all tokens | Submit all calls in one batch request |
| Prompt caching (BD + CS system prompts) | ~90% off system prompt tokens | 24K tokens cached at $0.03/MTok vs $0.25/MTok |
| `max_tokens` reduced 4,000 → 2,500 | ~13% output saving | Average response fits in ~1,800 tokens (2,500 gives headroom) |
| `MIN_WORDS` raised 100 → 150 | ~12% fewer API calls | Skips very short noise/dropped calls |
| **Combined saving vs baseline** | **~62% reduction** | $92/month → ~$35/month |

### 8.3 Reliability
- All stages are **idempotent** — re-running skips already-processed files
- **Retry logic** — Claude API errors retried 3x with exponential backoff (2s/4s/8s)
- **Batch resume** — interrupted batch polling resumes via `--batch-resume BATCH_ID` using `batch_state.json`
- **Transcription resume** — if transcription crashes, already-transcribed files are skipped on restart (checked by JSON existence)
- **Completeness check** — Stage 0 compares expected vs downloaded recordings; retries once if gap detected
- Execution state persisted to `pipeline_state.json` (last 30 runs)
- All output logged to timestamped files in `/logs/`

### 8.4 Security
- API keys stored in plaintext files, not in code — never commit key files to git
- Recordings contain PII (customer phone numbers, voices) — stored locally only
- GitHub Pages report must not contain raw transcripts or customer PII
- `_archive/` folder holds legacy data; can be deleted when no longer needed

### 8.5 Maintainability
- Agent lookup (`agents.py`) updated manually when agents join/leave
- Prompts versioned: `PROMPT_VERSION = "bd_v1"` / `"cs_v1"` — bump version if scoring rubric changes
- CSV schemas stable for Power BI compatibility — column names/order must not change without notice
- `prompt_version` stamped in every `*_analysis.json` for audit trail

---

## 9. Data Models

### 9.1 Analysis JSON (per-call)
```json
{
  "file": "19032026__Sachin_Gera__091530__CALLID_diarized.json",
  "call_metadata": {
    "timestamp": "2026-03-19 09:15:30",
    "agent_name": "Sachin Gera",
    "location": "Jaipur",
    "team": "BD Sales",
    "agent_status": "Active",
    "duration_sec": 299.95,
    "total_words": 623,
    "num_turns": 27,
    "language": "en"
  },
  "analysis": {
    "intent": {
      "primary_intent": "product_inquiry",
      "sub_intent": "availability_check",
      "urgency_level": "low",
      "competitor_mentioned": null,
      "upsell_signal_detected": true
    },
    "sentiment": {
      "overall": "positive",
      "score": 0.5,
      "opening_emotion": "curious",
      "closing_emotion": "satisfied",
      "churn_risk": "low"
    },
    "agent_scorecard": {
      "opening_greeting": 9,
      "needs_discovery": 8,
      "product_knowledge": 7,
      "objection_handling": 8,
      "closing_attempt": 9,
      "empathy_tone": 8,
      "communication_clarity": 9,
      "overall_score": 8.3,
      "strengths": ["Warm greeting", "Good product knowledge"],
      "improvement_areas": ["Could explore customization options"],
      "coaching_tip": "Proactively share WhatsApp catalog link",
      "missed_opportunity": "Did not ask for deposit to confirm store visit"
    },
    "call_outcome": {
      "resolved": true,
      "resolution_type": "store_visit_booked",
      "follow_up_required": false
    },
    "compliance": {
      "unauthorized_promise_made": false,
      "wrong_policy_info_given": false
    },
    "customer_voice": {
      "top_asks": ["Confirm availability before store visit"],
      "issues_raised": [],
      "product_service_gaps": ["No online dimension checker tool"],
      "unmet_needs": [],
      "positive_feedback": ["Agent was very knowledgeable"]
    },
    "talk_ratio": {
      "agent_percent": 58,
      "customer_percent": 42
    }
  },
  "prompt_version": "bd_v1",
  "analyzed_at": "2026-03-20T07:15:42"
}
```

### 9.2 Diarized Transcript JSON (per-call)
```json
{
  "metadata": {
    "file": "19032026__Sachin_Gera__091530__CALLID.mp3",
    "duration_sec": 299.95,
    "language": "en",
    "language_probability": 0.99,
    "model": "large-v3",
    "speakers": ["SPEAKER_00", "SPEAKER_01"],
    "num_turns": 27,
    "total_words": 623,
    "processed_at": "2026-03-19T22:15:00"
  },
  "turns": [
    {
      "speaker": "SPEAKER_00",
      "start": 0.123,
      "end": 5.456,
      "text": "Hello, thank you for calling WoodenStreet...",
      "word_count": 15,
      "avg_confidence": 0.95
    }
  ]
}
```

---

## 10. Directory Structure

```
/home/user/Documents/AI/SalesScorecard/
│
├── pipeline.py                  # Orchestrator — runs all stages (0→1→2→3→4)
├── ozonetel_fetcher.py          # Stage 0: fetch + download from Ozonetel
├── transcribe_diarize.py        # Stage 1: Whisper large-v3 + pyannote
├── analyze_calls.py             # Stage 2: Claude Haiku scoring (batch + real-time)
├── export_to_csv.py             # Stage 3: JSON → monthly/quarterly/master CSVs
├── generate_report.py           # Stage 3b: all analysis JSONs → standalone index.html
├── system_prompt_bd.py          # BD Sales prompt (bd_v1) — bump version if rubric changes
├── system_prompt_cs.py          # Customer Support prompt (cs_v1)
├── agents.py                    # Agent lookup: short name / W-ID → full details
├── serve.py                     # HTTP server + localhost.run tunnel
├── index.html                   # Generated HTML report (overwritten each run)
│
├── recording/
│   └── {YYYY-MM-DD}/            # Symlinks to ozonetel_archive recordings
│       └── done/                # Moved here after transcription
│
├── transcripts/
│   └── {YYYY-MM-DD}/            # *_diarized.json + *_diarized.txt
│
├── analysis/
│   └── {YYYY-MM-DD}/            # *_analysis.json per call
│
├── exports/
│   ├── {YYYY-MM}/               # Monthly CSVs (e.g. 2026-03/)
│   ├── {YYYY-QN}/               # Quarterly CSVs (e.g. 2026-Q1/)
│   └── master/                  # All-time union ← Power BI connects here
│       ├── calls.csv
│       ├── customer_voice.csv
│       ├── coaching.csv
│       ├── agent_scorecard.csv
│       └── daily_activity.csv
│
├── ozonetel_archive/
│   └── {YYYY-MM-DD}/
│       ├── metadata.csv         # One row per call (48 Ozonetel columns)
│       ├── stats.json           # Per-agent daily stats
│       └── recordings/          # Source MP3s (do not delete)
│
├── _archive/                    # Legacy data (safe to delete)
│   ├── transcripts_legacy/      # Pre-date-folder transcripts (Oct 2025)
│   └── analysis_legacy/         # Pre-date-folder analyses
│
├── logs/                        # pipeline_{timestamp}.log
├── batch_state.json             # Batch API resume state (auto-deleted when done)
├── pipeline_state.json          # Last 30 execution records
│
├── anthropic_key.txt            # Anthropic API key (do not commit)
├── github_token.txt             # GitHub OAuth token (do not commit)
└── huggingFaceToken_new.txt     # HuggingFace token for pyannote (do not commit)
```

---

## 11. Integrations

| Service | Purpose | Auth | Notes |
|---------|---------|------|-------|
| **Ozonetel** | Call metadata + MP3 download | Bearer token (JWT) | Endpoint: `callDetailsV2Report`, 48 columns, only accessible API |
| **Anthropic Claude Haiku 3** | Call analysis + scoring | API Key | Model: `claude-3-haiku-20240307`, Batch API default, prompt caching ON |
| **HuggingFace** | pyannote speaker diarization | User token | Model: `speaker-diarization-3.1` |
| **GitHub Pages** | Live HTML report hosting | OAuth token | Repo: `ashish-code-ch/woodenstreet-sales-report` |
| **Power BI** | Dashboard visualization | File-based | Reads CSVs from `exports/master/` |
| **localhost.run** | Public HTTPS tunnel for sharing | SSH | URL saved to `/tmp/live_url.txt` |

---

## 12. Agent Coverage

### BD Sales Team (34 agents across 4 locations)
| Location | Code | Count |
|----------|------|-------|
| Jaipur | JPR | ~13 agents |
| Udaipur | UDR | ~12 agents |
| Bangalore | BLR | ~6 agents |
| HSR Layout | HSR | ~3 agents |

### Customer Support Team (31 agents, Udaipur)
| Role | Count |
|------|-------|
| Support Leads | 3 |
| Inbound | 9 |
| Outbound | 11 |
| Carpentry | 8 |

All agents registered in `agents.py`. Two resolution methods:
- `resolve_agent(short_name)` — old filename format (e.g. `Sachin JPR`)
- `resolve_agent_by_fullname(name)` — new Ozonetel format (e.g. `Sachin_Gera`)

Team classification priority:
1. `campaignName` (`Outbound_Sales_*` → BD Sales, `Outbound_Support_*` → CS)
2. `skillName` (`Inbound_Sales`, `Inbound_Support`)
3. `agents.py` name lookup fallback

---

## 13. Known Gaps & Planned Improvements

### 13.1 Known Gaps

| Gap | Impact | Status |
|-----|--------|--------|
| Recordings stored locally only — no cloud backup | High — disk failure risk | Pending |
| Agent lookup requires manual update when staff changes | Medium — stale data if not maintained | Pending |
| `completeness_pct` shows ~80% due to talktime < 20s calls (not a real gap) | Low — confusing metric | Pending fix |
| ~~Report.html not auto-pushed to GitHub after pipeline run~~ | ~~Medium~~ | ✓ Fixed v1.3 (Stage 4) |
| ~~No retry logic if Claude API times out~~ | ~~Medium~~ | ✓ Fixed v1.1 |
| ~~Flat folder structure mixed all dates~~ | ~~Medium~~ | ✓ Fixed v1.2 |
| ~~Single prompt for BD + CS~~ | ~~High~~ | ✓ Fixed v1.1 |
| ~~No batch API — expensive real-time calls~~ | ~~High~~ | ✓ Fixed v1.2 |

### 13.2 Planned Enhancements (v2+)
- **Monthly/quarterly HTML report tabs** with period selector
- **Agent coaching digest** — WhatsApp/email summary to team leads every morning
- **Multi-date backfill** — run pipeline for a date range (e.g. last 7 days)
- **Web UI** for managers — filter by agent, date, intent, score range
- **CRM integration** — push call outcomes and follow-up flags to Salesforce/Zoho
- **Cloud backup** — sync `ozonetel_archive/` to S3 or GCS nightly
- **Churn prediction model** — predict conversion probability before call ends

---

## 14. Cron Schedule

```bash
# Daily at 7:00 AM — full pipeline (fetch yesterday → transcribe → analyse → export)
0 7 * * * cd /home/user/Documents/AI/SalesScorecard && \
           .venv/bin/python pipeline.py >> /tmp/pipeline_cron.log 2>&1
```

**Expected timeline:**
| Stage | Duration | Notes |
|-------|----------|-------|
| Stage 0 — Fetch | ~5 min | Downloads ~1,300 MP3s |
| Stage 1 — Transcribe | ~11 hours | GPU sequential, runs overnight |
| Stage 2 — Analyse (Batch) | ~15-30 min | ~900 calls, single batch submission |
| Stage 3 — Export | ~1 min | Generates all CSV tiers + index.html |
| Stage 4 — Push | ~1 min | Pushes index.html to GitHub Pages |
| **Total** | **Overnight** | Cron at 7 PM → report ready by 7 AM |

> **Recommendation:** Move cron to 7 PM to allow transcription overnight. Current 7 AM cron works if previous day's transcription already completed.

---

## 15. Glossary

| Term | Definition |
|------|-----------|
| **BD Sales** | Business Development Sales — outbound/inbound team converting leads to store visits and purchases |
| **CS** | Customer Support — handles complaints, delivery queries, returns, carpentry visits |
| **Hinglish** | Hindi-English code-switching — primary language of calls |
| **Diarization** | Process of identifying which speaker said what in an audio file |
| **Prompt Caching** | Claude API feature reusing system prompt tokens across calls (~90% token savings) |
| **Batch API** | Anthropic async batch processing — 50% discount, results within minutes to 24h |
| **prompt_version** | Version stamp (bd_v1 / cs_v1) in every analysis JSON — ensures consistency |
| **Escalation** | Call requiring supervisor intervention beyond first-line agent |
| **Customer Voice** | Signals from customer about their needs, issues, and gaps |
| **Conversion** | Customer commits to store visit, deposit, or purchase during the call |
| **3h Target** | BD Sales agents expected to accumulate ≥ 3 hours of talk time daily |
| **Lost Call** | Call that went unanswered or dropped before agent picked up |
| **Ozonetel** | Cloud PBX/telephony provider used by WoodenStreet |
| **pyannote** | Open-source speaker diarization library from HuggingFace |
| **JPR/UDR/BLR/HSR** | Location codes: Jaipur / Udaipur / Bangalore / HSR Layout |

---

*Version 1.3 — Updated 2026-03-21. Reflects implementation state as of this date.*
*Changes from v1.2: Stage 4 (GitHub push) added and made automatic; generate_report.py added; Operations Dashboard (R-05) added; call recording drill-through (R-06) added; team switcher (R-04) marked Done; max_tokens updated 2,000 → 2,500; directory structure updated; R-02 gap closed.*
*Next review: After first successful daily cron run.*
