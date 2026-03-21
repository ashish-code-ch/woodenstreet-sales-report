#!/usr/bin/env python3
"""
WoodenStreet Conversation Intelligence Pipeline
================================================
End-to-end daily pipeline:

  Stage 0 — FETCH        : Ozonetel API → download yesterday's MP3s + metadata
  Stage 1 — TRANSCRIBE   : Audio files → diarized JSON transcripts (Whisper + pyannote)
  Stage 2 — ANALYSE      : Transcripts → per-call analysis + auto-score (Claude Haiku)
  Stage 3 — EXPORT       : Analysis JSONs → flat CSVs for Power BI
  Stage 4 — PUSH         : index.html + source code → GitHub Pages (auto, mandatory)

Run modes:
  python pipeline.py                         # full pipeline (stages 0-4)
  python pipeline.py --stage fetch           # only Stage 0 (fetch today's recordings)
  python pipeline.py --stage fetch --date 2026-03-19   # fetch a specific date
  python pipeline.py --stage transcribe      # only Stage 1
  python pipeline.py --stage analyse        # only Stage 2
  python pipeline.py --stage export         # only Stage 3
  python pipeline.py --stage push           # only Stage 4 (push to GitHub)
  python pipeline.py --status               # show pipeline status

Daily cron (7 AM — installed via: crontab -e):
  0 7 * * * cd /home/user/Documents/AI/SalesScorecard && .venv/bin/python pipeline.py >> /tmp/pipeline_cron.log 2>&1
"""

import os, sys, json, subprocess, glob, argparse, shutil, tempfile
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = "/home/user/Documents/AI/SalesScorecard"
RECORDING_DIR   = os.path.join(BASE_DIR, "recording")
ARCHIVE_DIR     = os.path.join(BASE_DIR, "ozonetel_archive")
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")
ANALYSIS_DIR    = os.path.join(BASE_DIR, "analysis")
EXPORTS_DIR     = os.path.join(BASE_DIR, "exports")
LOG_DIR         = os.path.join(BASE_DIR, "logs")
STATE_FILE      = os.path.join(BASE_DIR, "pipeline_state.json")
PYTHON          = os.path.join(BASE_DIR, ".venv", "bin", "python")

GITHUB_USER     = "ashish-code-ch"
GITHUB_REPO     = "woodenstreet-sales-report"
GITHUB_TOKEN_FILE = os.path.join(BASE_DIR, "github_token.txt")

# Source files to back up to GitHub (code subfolder in the report repo)
SOURCE_FILES = [
    "pipeline.py", "analyze_calls.py", "transcribe_diarize.py",
    "export_to_csv.py", "ozonetel_fetcher.py", "agents.py",
    "system_prompt_bd.py", "system_prompt_cs.py", "PRD.md",
]

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


# ── Stage 0: Fetch from Ozonetel ──────────────────────────────────────────────

def stage_fetch(logger: "PipelineLogger", target_date: str = None) -> dict:
    """
    Stage 0 — Ozonetel fetch:
      1. Download call metadata + MP3s for target_date (default: yesterday).
      2. Completeness check: compare recordings_available in stats.json vs
         actual .mp3 files on disk. If gap > 0, retry once.
      3. Symlink any new recordings into /recording/ so Stage 1 picks them up.
         (Symlinks avoid copying — /recording/ stays clean; archive is the source.)
    """
    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.section(f"STAGE 0 — FETCH  (Ozonetel → {target_date})")

    ok = run_stage(
        os.path.join(BASE_DIR, "ozonetel_fetcher.py"),
        ["--date", target_date],
        logger,
    )
    if not ok:
        return {"stage": "fetch", "status": "error", "date": target_date,
                "downloaded": 0, "staged": 0}

    archive_rec_dir = Path(ARCHIVE_DIR) / target_date / "recordings"
    stats_file      = Path(ARCHIVE_DIR) / target_date / "stats.json"

    # ── Completeness check ────────────────────────────────────────────────────
    actual = len(list(archive_rec_dir.glob("*.mp3"))) if archive_rec_dir.exists() else 0
    expected = 0
    if stats_file.exists():
        try:
            stats = json.loads(stats_file.read_text())
            expected = sum(v.get("recordings_available", 0) for v in stats.values()
                           if isinstance(v, dict))
        except Exception:
            pass

    logger.log(f"  Completeness: expected {expected}, downloaded {actual}")

    if expected > 0 and actual < expected:
        gap = expected - actual
        logger.log(f"  Gap of {gap} recording(s) detected — retrying failed downloads...")
        run_stage(
            os.path.join(BASE_DIR, "ozonetel_fetcher.py"),
            ["--date", target_date],
            logger,
        )
        actual = len(list(archive_rec_dir.glob("*.mp3"))) if archive_rec_dir.exists() else 0
        logger.log(f"  After retry: {actual}/{expected} recordings on disk")

    # ── Symlink new recordings into recording/{date}/ for Stage 1 ───────────
    rec_dir = Path(RECORDING_DIR) / target_date
    rec_dir.mkdir(parents=True, exist_ok=True)
    staged = 0
    already_staged = 0

    if archive_rec_dir.exists():
        for mp3 in sorted(archive_rec_dir.glob("*.mp3")):
            link = rec_dir / mp3.name
            done = rec_dir / "done" / mp3.name
            if done.exists():
                already_staged += 1   # already processed in a prior run
            elif not link.exists():
                link.symlink_to(mp3.resolve())
                staged += 1

    logger.log(f"  Staged {staged} new recording(s) → /recording/  "
               f"({already_staged} already processed in prior runs)")

    return {
        "stage":           "fetch",
        "status":          "ok",
        "date":            target_date,
        "downloaded":      actual,
        "expected":        expected,
        "completeness_pct": round(actual / expected * 100, 1) if expected else 100,
        "staged":          staged,
    }


# ── Stage helpers ─────────────────────────────────────────────────────────────

def count_pending_audio() -> int:
    """Count audio files waiting in recording/YYYY-MM-DD/ (excluding done/ subfolders)."""
    total = 0
    for day_dir in Path(RECORDING_DIR).iterdir():
        if not day_dir.is_dir() or day_dir.name == "done":
            continue
        total += sum(
            1 for p in day_dir.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        )
    return total


def count_pending_transcripts() -> int:
    """Count transcripts that don't have a corresponding analysis JSON yet."""
    transcripts = (
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")) +              # flat (legacy)
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "????-??-??", "*_diarized.json"))  # dated subdirs
    )
    done = set(
        Path(f).stem.replace("_analysis", "")
        for f in (
            glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")) +
            glob.glob(os.path.join(ANALYSIS_DIR, "????-??-??", "*_analysis.json"))
        )
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

def stage_transcribe(logger: PipelineLogger, target_date: str = None) -> dict:
    logger.section("STAGE 1 — TRANSCRIBE  (Whisper large-v3 + pyannote)")

    pending = count_pending_audio()
    if pending == 0:
        logger.log("  No new audio files in recording/ date folders — skipping transcription.")
        return {"stage": "transcribe", "status": "skipped", "new_files": 0}

    logger.log(f"  Found {pending} new audio file(s) to transcribe.")

    extra_args = ["--date", target_date] if target_date else []

    before = len(
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")) +
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "????-??-??", "*_diarized.json"))
    )
    ok = run_stage(
        os.path.join(BASE_DIR, "transcribe_diarize.py"),
        extra_args,
        logger,
    )
    after = len(
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "*_diarized.json")) +
        glob.glob(os.path.join(TRANSCRIPTS_DIR, "????-??-??", "*_diarized.json"))
    )
    new_transcripts = after - before

    return {
        "stage":           "transcribe",
        "status":          "ok" if ok else "error",
        "audio_found":     pending,
        "new_transcripts": new_transcripts,
    }


def stage_analyse(logger: PipelineLogger, from_date: str = None) -> dict:
    logger.section("STAGE 2 — ANALYSE & AUTO-SCORE  (Claude Haiku)")

    pending = count_pending_transcripts()
    if pending == 0:
        logger.log("  All transcripts already analysed — skipping.")
        return {"stage": "analyse", "status": "skipped", "new_analyses": 0}

    logger.log(f"  Found {pending} transcript(s) to analyse.")
    if from_date:
        logger.log(f"  Date filter: {from_date} and later (older files skipped)")

    extra_args = ["--batch", "--from-date", from_date] if from_date else ["--batch"]

    before = len(
        glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")) +
        glob.glob(os.path.join(ANALYSIS_DIR, "????-??-??", "*_analysis.json"))
    )
    ok = run_stage(
        os.path.join(BASE_DIR, "analyze_calls.py"),
        extra_args,
        logger,
    )
    after = len(
        glob.glob(os.path.join(ANALYSIS_DIR, "*_analysis.json")) +
        glob.glob(os.path.join(ANALYSIS_DIR, "????-??-??", "*_analysis.json"))
    )
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


# ── Stage 4: GitHub push ──────────────────────────────────────────────────────

def stage_push(logger: "PipelineLogger", target_date: str = None) -> dict:
    """
    Stage 4 — GitHub Push:
      1. Clone/pull the GitHub Pages report repo.
      2. Copy index.html (report dashboard) to the repo root.
      3. Copy source .py + PRD.md into a /code subfolder.
      4. Commit with date-stamped message and push.
    """
    logger.section("STAGE 4 — GITHUB PUSH  (report + source backup)")

    # Load token
    if not os.path.isfile(GITHUB_TOKEN_FILE):
        logger.log("  ERROR: github_token.txt not found — skipping push.")
        return {"stage": "push", "status": "error", "reason": "token missing"}
    token = open(GITHUB_TOKEN_FILE).read().strip()
    remote = f"https://{GITHUB_USER}:{token}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    # Determine what to push
    report_src = os.path.join(BASE_DIR, "index.html")
    if not os.path.isfile(report_src):
        logger.log("  WARNING: index.html not found — report will not be updated.")
        report_src = None

    repo_dir = os.path.join(tempfile.gettempdir(), "ws-report")

    try:
        # Clone or pull
        if os.path.isdir(os.path.join(repo_dir, ".git")):
            logger.log("  Pulling latest from GitHub Pages repo...")
            result = subprocess.run(
                ["git", "-C", repo_dir, "pull", "--rebase", "--autostash"],
                capture_output=True, text=True
            )
        else:
            logger.log("  Cloning GitHub Pages repo...")
            shutil.rmtree(repo_dir, ignore_errors=True)
            result = subprocess.run(
                ["git", "clone", remote, repo_dir],
                capture_output=True, text=True
            )
        if result.returncode != 0:
            logger.log(f"  ERROR: git clone/pull failed:\n{result.stderr}")
            return {"stage": "push", "status": "error", "reason": result.stderr[:200]}

        # Copy report dashboard
        changed = []
        if report_src:
            shutil.copy2(report_src, os.path.join(repo_dir, "index.html"))
            changed.append("index.html")
            logger.log("  Copied index.html → repo root")

        # Copy source files into code/ subfolder
        code_dir = os.path.join(repo_dir, "code")
        os.makedirs(code_dir, exist_ok=True)
        for fname in SOURCE_FILES:
            src = os.path.join(BASE_DIR, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(code_dir, fname))
                changed.append(f"code/{fname}")
        logger.log(f"  Copied {len(SOURCE_FILES)} source files → code/")

        if not changed:
            logger.log("  Nothing to push.")
            return {"stage": "push", "status": "skipped", "reason": "no files"}

        # Stage all changes
        subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True, capture_output=True)

        # Check if there's actually anything to commit
        diff = subprocess.run(
            ["git", "-C", repo_dir, "diff", "--cached", "--stat"],
            capture_output=True, text=True
        )
        if not diff.stdout.strip():
            logger.log("  No changes detected — nothing to commit.")
            return {"stage": "push", "status": "skipped", "reason": "no changes"}

        # Commit
        date_label = target_date or datetime.now().strftime("%Y-%m-%d")
        commit_msg = f"Auto-update: pipeline run {date_label} ({datetime.now().strftime('%H:%M')})"
        subprocess.run(
            ["git", "-C", repo_dir, "-c", "user.name=WS Pipeline",
             "-c", "user.email=pipeline@woodenstreet.com",
             "commit", "-m", commit_msg],
            check=True, capture_output=True
        )
        logger.log(f"  Committed: {commit_msg}")

        # Push
        push = subprocess.run(
            ["git", "-C", repo_dir, "push", remote, "HEAD"],
            capture_output=True, text=True
        )
        if push.returncode != 0:
            logger.log(f"  ERROR: git push failed:\n{push.stderr}")
            return {"stage": "push", "status": "error", "reason": push.stderr[:200]}

        url = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/"
        logger.log(f"  Pushed successfully → {url}")
        return {"stage": "push", "status": "ok", "url": url, "files_updated": len(changed)}

    except Exception as e:
        logger.log(f"  ERROR: {e}")
        return {"stage": "push", "status": "error", "reason": str(e)}


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
        choices=["fetch", "transcribe", "analyse", "export", "push"],
        help="Run only one stage instead of the full pipeline"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date for Stage 0 fetch (YYYY-MM-DD). Default: yesterday."
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
        # target_date drives both fetch and the analyse date-filter
        target_date = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        if args.stage in (None, "fetch"):
            results.append(stage_fetch(logger, target_date=target_date))

        if args.stage in (None, "transcribe"):
            results.append(stage_transcribe(logger, target_date=target_date))

        if args.stage in (None, "analyse"):
            results.append(stage_analyse(logger, from_date=target_date))

        if args.stage in (None, "export"):
            results.append(stage_export(logger))

        if args.stage in (None, "push"):
            results.append(stage_push(logger, target_date=target_date))

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
        if r["stage"] == "fetch":
            detail = (f"  {r.get('downloaded', 0)}/{r.get('expected', '?')} recordings"
                      f"  ({r.get('completeness_pct', '?')}%)  → {r.get('staged', 0)} staged")
        elif r["stage"] == "transcribe" and status != "skipped":
            detail = f"  +{r.get('new_transcripts', 0)} transcripts"
        elif r["stage"] == "analyse" and status != "skipped":
            detail = f"  +{r.get('new_analyses', 0)} analyses  (total {r.get('total_done', 0)})"
        elif r["stage"] == "export":
            detail = f"  {len(r.get('csv_files', []))} CSV files → {r.get('export_dir', '')}"
        elif r["stage"] == "push" and status == "ok":
            detail = f"  {r.get('files_updated', 0)} files → {r.get('url', '')}"
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
