#!/usr/bin/env python3
"""
WoodenStreet Conversation Intelligence Pipeline
================================================
End-to-end daily pipeline:

  Stage 1 — TRANSCRIBE   : Audio files → diarized JSON transcripts (Whisper + pyannote)
  Stage 2 — ANALYSE      : Transcripts → per-call analysis + auto-score (Claude Haiku)
  Stage 3 — EXPORT       : Analysis JSONs → flat CSVs for Power BI

Run modes:
  python pipeline.py                    # full pipeline (all 3 stages)
  python pipeline.py --stage transcribe # only Stage 1
  python pipeline.py --stage analyse    # only Stage 2
  python pipeline.py --stage export     # only Stage 3
  python pipeline.py --status           # show pipeline status (counts, last run)

Daily cron (runs at 7 AM):
  0 7 * * * cd /home/user/Documents/AI/SalesScorecard && .venv/bin/python pipeline.py >> /tmp/pipeline_cron.log 2>&1
"""

import os, sys, json, subprocess, glob, argparse
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = "/home/user/Documents/AI/SalesScorecard"
RECORDING_DIR   = os.path.join(BASE_DIR, "recording")
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")
ANALYSIS_DIR    = os.path.join(BASE_DIR, "analysis")
EXPORTS_DIR     = os.path.join(BASE_DIR, "exports")
LOG_DIR         = os.path.join(BASE_DIR, "logs")
STATE_FILE      = os.path.join(BASE_DIR, "pipeline_state.json")
PYTHON          = os.path.join(BASE_DIR, ".venv", "bin", "python")

AUDIO_EXTS = {".mp3", ".mpeg", ".mp4", ".m4a", ".wav", ".ogg",
              ".flac", ".aac", ".wma", ".opus", ".webm"}
# ─────────────────────────────────────────────────────────────────────────────


class PipelineLogger:
    """Writes to both console and a timestamped log file."""

    def __init__(self, log_dir: str):
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"pipeline_{ts}.log")
        self._f = open(self.log_path, "w", encoding="utf-8")

    def log(self, msg: str = ""):
        print(msg, flush=True)
        self._f.write(msg + "\n")
        self._f.flush()

    def close(self):
        self._f.close()

    def section(self, title: str):
        bar = "═" * 60
        self.log(f"\n{bar}")
        self.log(f"  {title}")
        self.log(bar)


# ── State tracking ────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.isfile(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            pass
    return {"runs": [], "last_run": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Stage helpers ─────────────────────────────────────────────────────────────

def count_pending_audio() -> int:
    """Count audio files waiting in /recording/ (excluding done/ subfolder)."""
    done_dir = os.path.join(RECORDING_DIR, "done")
    return sum(
        1 for p in Path(RECORDING_DIR).iterdir()
        if p.is_file()
        and p.suffix.lower() in AUDIO_EXTS
        and str(p.parent) != done_dir
    )


def count_pending_transcripts() -> int:
    """Count transcripts that don't have a corresponding analysis JSON yet."""
    transcripts = glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json"))
    done = set(
        Path(f).stem.replace("_diarized", "")
        for f in glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json"))
    )
    return sum(
        1 for t in transcripts
        if Path(t).stem.replace("_diarized", "") not in done
    )


def run_stage(script: str, args: list, logger: PipelineLogger) -> bool:
    """Run a pipeline script as a subprocess, streaming its output."""
    cmd = [PYTHON, script] + args
    logger.log(f"  Running: {' '.join(cmd)}")
    logger.log("")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=BASE_DIR,
    )
    for line in proc.stdout:
        logger.log(line.rstrip())
    proc.wait()

    if proc.returncode != 0:
        logger.log(f"\n  ERROR: Script exited with code {proc.returncode}")
        return False
    return True


# ── Pipeline stages ───────────────────────────────────────────────────────────

def stage_transcribe(logger: PipelineLogger) -> dict:
    logger.section("STAGE 1 — TRANSCRIBE  (Whisper large-v3 + pyannote)")

    pending = count_pending_audio()
    if pending == 0:
        logger.log("  No new audio files in /recording/ — skipping transcription.")
        return {"stage": "transcribe", "status": "skipped", "new_files": 0}

    logger.log(f"  Found {pending} new audio file(s) to transcribe.")

    before = len(glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")))
    ok = run_stage(
        os.path.join(BASE_DIR, "transcribe_diarize.py"),
        [],
        logger,
    )
    after = len(glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")))
    new_transcripts = after - before

    return {
        "stage":           "transcribe",
        "status":          "ok" if ok else "error",
        "audio_found":     pending,
        "new_transcripts": new_transcripts,
    }


def stage_analyse(logger: PipelineLogger) -> dict:
    logger.section("STAGE 2 — ANALYSE & AUTO-SCORE  (Claude Haiku)")

    pending = count_pending_transcripts()
    if pending == 0:
        logger.log("  All transcripts already analysed — skipping.")
        return {"stage": "analyse", "status": "skipped", "new_analyses": 0}

    logger.log(f"  Found {pending} transcript(s) to analyse.")

    before = len(glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")))
    ok = run_stage(
        os.path.join(BASE_DIR, "analyze_calls.py"),
        [],
        logger,
    )
    after = len(glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")))
    new_analyses = after - before

    return {
        "stage":        "analyse",
        "status":       "ok" if ok else "error",
        "new_analyses": new_analyses,
        "total_done":   after,
    }


def stage_export(logger: PipelineLogger) -> dict:
    logger.section("STAGE 3 — EXPORT TO CSV  (Power BI ready)")

    ok = run_stage(
        os.path.join(BASE_DIR, "export_to_csv.py"),
        [],
        logger,
    )

    csv_files = glob.glob(os.path.join(EXPORTS_DIR, "*.csv"))
    return {
        "stage":      "export",
        "status":     "ok" if ok else "error",
        "csv_files":  [os.path.basename(f) for f in csv_files],
        "export_dir": EXPORTS_DIR,
    }


# ── Status report ─────────────────────────────────────────────────────────────

def show_status():
    state = load_state()

    audio_waiting    = count_pending_audio()
    total_transcripts= len(glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")))
    pending_analysis = count_pending_transcripts()
    total_analyses   = len(glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")))
    csv_files        = glob.glob(os.path.join(EXPORTS_DIR, "*.csv"))

    print("\n" + "═" * 60)
    print("  WOODENSTREET PIPELINE STATUS")
    print("═" * 60)
    print(f"\n  Stage 1 — Transcribe")
    print(f"    Audio waiting in /recording/  : {audio_waiting}")
    print(f"    Transcripts completed          : {total_transcripts}")
    print(f"\n  Stage 2 — Analyse")
    print(f"    Pending analysis               : {pending_analysis}")
    print(f"    Analyses completed             : {total_analyses}")
    print(f"\n  Stage 3 — Export")
    print(f"    CSV files in /exports/         : {len(csv_files)}")
    for f in sorted(csv_files):
        size_kb = os.path.getsize(f) // 1024
        mtime   = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        print(f"      {os.path.basename(f):30s}  {size_kb:>5} KB  (updated {mtime})")

    if state.get("last_run"):
        print(f"\n  Last full run : {state['last_run']}")
    if state.get("runs"):
        last = state["runs"][-1]
        print(f"  Last run result:")
        for stage_result in last.get("stages", []):
            status = stage_result.get("status", "?")
            icon   = "✓" if status == "ok" else ("–" if status == "skipped" else "✗")
            print(f"    {icon}  {stage_result.get('stage','?'):12s}  {status}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WoodenStreet Conversation Intelligence Pipeline")
    parser.add_argument(
        "--stage",
        choices=["transcribe", "analyse", "export"],
        help="Run only one stage instead of the full pipeline"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show pipeline status and exit"
    )
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    logger = PipelineLogger(LOG_DIR)
    started_at = datetime.now()

    logger.log("═" * 60)
    logger.log("  WOODENSTREET CONVERSATION INTELLIGENCE PIPELINE")
    logger.log(f"  Started : {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log("═" * 60)

    results = []

    try:
        if args.stage in (None, "transcribe"):
            results.append(stage_transcribe(logger))

        if args.stage in (None, "analyse"):
            results.append(stage_analyse(logger))

        if args.stage in (None, "export"):
            results.append(stage_export(logger))

    except KeyboardInterrupt:
        logger.log("\n  Pipeline interrupted by user.")

    # ── Final summary ─────────────────────────────────────────────────────────
    finished_at = datetime.now()
    elapsed     = (finished_at - started_at).seconds

    logger.section("PIPELINE SUMMARY")
    for r in results:
        status = r.get("status", "?")
        icon   = "✓" if status == "ok" else ("–" if status == "skipped" else "✗")
        detail = ""
        if r["stage"] == "transcribe" and status != "skipped":
            detail = f"  +{r.get('new_transcripts', 0)} transcripts"
        elif r["stage"] == "analyse" and status != "skipped":
            detail = f"  +{r.get('new_analyses', 0)} analyses  (total {r.get('total_done', 0)})"
        elif r["stage"] == "export":
            detail = f"  {len(r.get('csv_files', []))} CSV files → {r.get('export_dir', '')}"
        logger.log(f"  {icon}  {r['stage']:12s}  {status}{detail}")

    logger.log(f"\n  Elapsed : {elapsed}s")
    logger.log(f"  Log     : {logger.log_path}")
    logger.log("═" * 60 + "\n")

    # ── Persist run to state ──────────────────────────────────────────────────
    state = load_state()
    state["last_run"] = finished_at.isoformat(timespec="seconds")
    state.setdefault("runs", []).append({
        "started_at":  started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_sec": elapsed,
        "stages":      results,
    })
    state["runs"] = state["runs"][-30:]  # keep last 30 runs
    save_state(state)

    logger.close()

    # Exit with error code if any stage failed
    if any(r.get("status") == "error" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
