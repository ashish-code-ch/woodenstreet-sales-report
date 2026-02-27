#!/usr/bin/env python3
"""
High-accuracy audio transcription + speaker diarization.
  - faster-whisper large-v3  (word-level timestamps, tuned VAD)
  - pyannote/speaker-diarization-3.1  (GPU, pyannote 4.x API)
  - Word-level speaker alignment  (much more precise than segment-level)
  - Rich JSON output: turns with per-word timestamps, confidence, stats
"""

import os, sys, json, warnings
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# ── Config ───────────────────────────────────────────────────────────────────
AUDIO_FILE   = "/home/user/Documents/AI/SalesScorecard/recording/20250905-185051_919151117439_3245-all.mp3"
OUTPUT_DIR   = "/home/user/Documents/AI/SalesScorecard/transcripts"
HF_TOKEN     = open("/home/user/Documents/AI/SalesScorecard/huggingFaceToken_new.txt").read().strip()

WHISPER_MODEL = "large-v3"
COMPUTE_TYPE  = "float16"   # float16 on GPU | int8 on CPU
DEVICE        = "cuda"

# Speaker count — set NUM_SPEAKERS to exact int if known, else use min/max
# For a known 2-speaker call (agent + customer), set this to 2 for better boundaries
NUM_SPEAKERS  = 2
MIN_SPEAKERS  = 2
MAX_SPEAKERS  = 4

LANGUAGE       = None        # None = auto-detect per segment (handles Hindi/English switching)
TASK           = "translate" # "translate" → always output English regardless of input lang
                             # "transcribe" → output in original language
INITIAL_PROMPT = "This is a customer support call about furniture orders. Agent and customer alternate speaking."

# VAD — tighter than before so noise/hold-music is NOT passed to Whisper
VAD_PARAMS = dict(
    threshold                = 0.5,    # raised from 0.4; filters more noise
    min_speech_duration_ms   = 200,
    max_speech_duration_s    = float("inf"),
    min_silence_duration_ms  = 600,    # still loose enough for code-switching pauses
    speech_pad_ms            = 400,
)

# Anti-hallucination thresholds
# compression_ratio: text with ratio > threshold is likely a repetition loop (e.g. "Yes Yes Yes")
# log_prob_threshold: segments with avg log-prob below this are too uncertain → discard
# no_speech_threshold: if no-speech prob > this → discard the segment entirely
# hallucination_silence_threshold: suppress output for silence gaps longer than N seconds
COMPRESSION_RATIO_THRESHOLD    = 1.8   # default 2.4; stricter = fewer repetition hallucinations
LOG_PROB_THRESHOLD             = -1.0
NO_SPEECH_THRESHOLD            = 0.6
HALLUCINATION_SILENCE_THRESHOLD = 2.0  # key param: kills "camera" style insertions in pauses
REPETITION_PENALTY             = 1.3   # penalise generating the same token again
NO_REPEAT_NGRAM_SIZE           = 5     # block repeating any 5-gram
# ─────────────────────────────────────────────────────────────────────────────

WHISPER_SR = 16000


def fmt_time(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    ms     = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def load_audio(path: str, target_sr: int):
    """Load audio via PyAV (bundled FFmpeg). Returns (ndarray channels×samples, orig_sr)."""
    import av
    container = av.open(path)
    stream    = container.streams.audio[0]
    orig_sr   = stream.sample_rate

    chunks = []
    for frame in container.decode(audio=0):
        arr = frame.to_ndarray()
        if arr.ndim == 1:
            arr = arr[np.newaxis, :]
        chunks.append(arr.astype(np.float32))
    audio = np.concatenate(chunks, axis=1)   # (channels, samples)

    if orig_sr != target_sr:
        import torch, torchaudio.functional as F
        audio = F.resample(torch.from_numpy(audio), orig_sr, target_sr).numpy()

    return audio, orig_sr


# ── Step 1: Transcribe with word timestamps ───────────────────────────────────
def transcribe(audio_mono: np.ndarray):
    """
    Run faster-whisper and return:
      - flat list of words: {word, start, end, confidence}
      - transcription info dict
    """
    from faster_whisper import WhisperModel

    print(f"[1/3] Loading faster-whisper/{WHISPER_MODEL} on {DEVICE} ...", flush=True)
    model = WhisperModel(WHISPER_MODEL, device=DEVICE, compute_type=COMPUTE_TYPE)

    dur = len(audio_mono) / WHISPER_SR
    print(f"[1/3] Transcribing {dur:.1f} sec (task={TASK}, lang=auto) ...", flush=True)

    segments_gen, info = model.transcribe(
        audio_mono,
        language=LANGUAGE,
        task=TASK,
        initial_prompt=INITIAL_PROMPT,
        beam_size=5,
        best_of=1,                              # only meaningful with temperature > 0
        word_timestamps=True,
        condition_on_previous_text=False,       # ← KEY: prevents cascade hallucinations
        vad_filter=True,
        vad_parameters=VAD_PARAMS,
        temperature=0.0,                        # greedy only — fallback temps add hallucinations
        compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
        log_prob_threshold=LOG_PROB_THRESHOLD,
        no_speech_threshold=NO_SPEECH_THRESHOLD,
        hallucination_silence_threshold=HALLUCINATION_SILENCE_THRESHOLD,
        repetition_penalty=REPETITION_PENALTY,
        no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
    )

    words = []
    seg_count = 0
    for seg in segments_gen:
        seg_count += 1
        if not seg.words:
            continue
        for w in seg.words:
            words.append({
                "word":       w.word,
                "start":      round(w.start, 3),
                "end":        round(w.end, 3),
                "confidence": round(w.probability, 3),
            })

    print(f"      Language: {info.language} ({info.language_probability:.2f}) "
          f"| Segments: {seg_count} | Words: {len(words)}", flush=True)

    transcription_info = {
        "language":             info.language,
        "language_probability": round(info.language_probability, 4),
        "duration":             round(info.duration, 3),
        "model":                f"faster-whisper/{WHISPER_MODEL}",
    }
    return words, transcription_info


# ── Step 2: Speaker diarization ───────────────────────────────────────────────
def diarize(audio_np: np.ndarray, orig_sr: int):
    """
    Run pyannote 4.x diarization using pre-loaded waveform tensor.
    Returns list of {start, end, speaker}.
    """
    import torch
    from pyannote.audio import Pipeline

    print("[2/3] Loading pyannote/speaker-diarization-3.1 ...", flush=True)
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=HF_TOKEN,
    )
    pipeline.to(torch.device(DEVICE))

    waveform = torch.from_numpy(audio_np)
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    audio_input = {"waveform": waveform, "sample_rate": orig_sr}

    print("[2/3] Running diarization ...", flush=True)
    kwargs = {"num_speakers": NUM_SPEAKERS} if NUM_SPEAKERS else \
             {"min_speakers": MIN_SPEAKERS, "max_speakers": MAX_SPEAKERS}
    result = pipeline(audio_input, **kwargs)

    # Use exclusive_speaker_diarization (no overlaps) — best for transcription alignment
    annotation = result.exclusive_speaker_diarization
    speaker_segs = [
        {"start": t.start, "end": t.end, "speaker": spk}
        for t, _, spk in annotation.itertracks(yield_label=True)
    ]

    unique = sorted({s["speaker"] for s in speaker_segs})
    print(f"      Speaker segments: {len(speaker_segs)} | "
          f"Speakers detected: {len(unique)} — {unique}", flush=True)
    return speaker_segs


# ── Step 3: Word-level speaker alignment ──────────────────────────────────────
def assign_speaker_to_word(word: dict, speaker_segs: list) -> str:
    """Find the speaker with maximum time-overlap with this word."""
    ws, we = word["start"], word["end"]
    best_spk, best_ov = "UNKNOWN", 0.0
    for sp in speaker_segs:
        ov = max(0.0, min(we, sp["end"]) - max(ws, sp["start"]))
        if ov > best_ov:
            best_ov, best_spk = ov, sp["speaker"]

    if best_spk != "UNKNOWN":
        return best_spk

    # Word falls in a gap between segments — use the immediately preceding
    # segment (speaker who just spoke tends to keep speaking across tiny gaps)
    mid = (ws + we) / 2
    before = [s for s in speaker_segs if s["end"] <= ws]
    after  = [s for s in speaker_segs if s["start"] >= we]
    if before and after:
        prev_seg = max(before, key=lambda s: s["end"])
        next_seg = min(after,  key=lambda s: s["start"])
        gap_to_prev = mid - prev_seg["end"]
        gap_to_next = next_seg["start"] - mid
        # Bias toward previous speaker: only switch if next segment is clearly closer
        best_spk = prev_seg["speaker"] if gap_to_prev <= gap_to_next * 1.5 else next_seg["speaker"]
    elif before:
        best_spk = max(before, key=lambda s: s["end"])["speaker"]
    elif after:
        best_spk = min(after, key=lambda s: s["start"])["speaker"]
    else:
        best_spk = min(speaker_segs, key=lambda s: abs((s["start"] + s["end"]) / 2 - mid))["speaker"]
    return best_spk


def build_turns(words: list, speaker_segs: list) -> list:
    """
    1. Assign speaker to every word.
    2. Group consecutive same-speaker words into turns.
    3. Annotate each turn with stats.
    """
    # Assign speakers
    for w in words:
        w["speaker"] = assign_speaker_to_word(w, speaker_segs)

    if not words:
        return []

    # Group into turns
    turns = []
    cur_words = [words[0]]
    for w in words[1:]:
        if w["speaker"] == cur_words[-1]["speaker"]:
            cur_words.append(w)
        else:
            turns.append(_make_turn(cur_words))
            cur_words = [w]
    turns.append(_make_turn(cur_words))

    return turns


def _make_turn(words: list) -> dict:
    text = " ".join(w["word"].strip() for w in words)
    text = " ".join(text.split())   # normalise whitespace
    avg_conf = round(sum(w["confidence"] for w in words) / len(words), 3)
    return {
        "speaker":        words[0]["speaker"],
        "start":          words[0]["start"],
        "end":            words[-1]["end"],
        "duration":       round(words[-1]["end"] - words[0]["start"], 3),
        "text":           text,
        "word_count":     len(words),
        "avg_confidence": avg_conf,
        "words":          words,
    }


# ── Post-processing: remove hallucination turns ───────────────────────────────
import re as _re

def _is_repetition_hallucination(text: str) -> bool:
    """Detect 'Yes. Yes. Yes.' style repetition loops."""
    words = text.split()
    if len(words) < 6:
        return False
    # If any single word makes up >50% of all words it's a loop
    from collections import Counter
    top_word, top_count = Counter(words).most_common(1)[0]
    return top_count / len(words) > 0.5

def filter_hallucinations(turns: list) -> list:
    """Remove turns that are clearly hallucinations."""
    clean = []
    removed = 0
    for t in turns:
        if _is_repetition_hallucination(t["text"]):
            print(f"      [REMOVED hallucination] {t['speaker']} "
                  f"[{fmt_time(t['start'])}] {t['text'][:60]}…", flush=True)
            removed += 1
        elif t["avg_confidence"] < 0.30 and t["word_count"] > 3:
            print(f"      [REMOVED low-confidence] {t['speaker']} "
                  f"[{fmt_time(t['start'])}] conf={t['avg_confidence']} {t['text'][:60]}…", flush=True)
            removed += 1
        else:
            clean.append(t)
    if removed:
        print(f"      Hallucination filter: removed {removed} turns", flush=True)
    return clean


# ── Post-processing: fix fragmented diarization ───────────────────────────────
def _merge_two_turns(t1: dict, t2: dict) -> dict:
    """Merge t2 into t1 (keep t1's speaker for all words)."""
    all_words = t1["words"] + t2["words"]
    for w in all_words:
        w["speaker"] = t1["speaker"]
    return _make_turn(all_words)


def fix_fragmented_turns(turns: list,
                         sandwich_min_words: int = 5,
                         edge_min_words: int = 3) -> list:
    """
    Fix diarization fragmentation caused by imprecise pyannote boundaries.

    Three passes (iterated until stable):
      1. Sandwich: A-B-A where B has ≤ sandwich_min_words → merge all into A.
         Uses a slightly higher threshold than edges because mid-conversation
         boundary errors tend to produce slightly longer mis-attributed segments.
      2. Consecutive same-speaker turns → merge (handles pass-1 side-effects).
      3. Leading/trailing micro-turns: if a turn has ≤ edge_min_words and it's
         the first or last turn, merge with its neighbor.
    """
    if not turns:
        return turns

    merged_count = 0
    for _ in range(10):  # iterate until stable
        changed = False

        # Pass 1: A-B-A sandwich
        new_turns = []
        i = 0
        while i < len(turns):
            if (i + 2 < len(turns) and
                    turns[i]["speaker"] == turns[i + 2]["speaker"] and
                    turns[i + 1]["word_count"] <= sandwich_min_words):
                base = _merge_two_turns(turns[i], turns[i + 1])
                merged = _merge_two_turns(base, turns[i + 2])
                new_turns.append(merged)
                merged_count += 2
                i += 3
                changed = True
            else:
                new_turns.append(turns[i])
                i += 1
        turns = new_turns

        # Pass 2: consecutive same-speaker
        new_turns = []
        for t in turns:
            if new_turns and new_turns[-1]["speaker"] == t["speaker"]:
                new_turns[-1] = _merge_two_turns(new_turns[-1], t)
                merged_count += 1
                changed = True
            else:
                new_turns.append(t)
        turns = new_turns

        # Pass 3: micro leading/trailing turns
        if len(turns) >= 2 and turns[0]["word_count"] <= edge_min_words:
            merged_words = turns[0]["words"] + turns[1]["words"]
            for w in merged_words:
                w["speaker"] = turns[1]["speaker"]
            turns = [_make_turn(merged_words)] + turns[2:]
            merged_count += 1
            changed = True
        if len(turns) >= 2 and turns[-1]["word_count"] <= edge_min_words:
            merged_words = turns[-2]["words"] + turns[-1]["words"]
            for w in merged_words:
                w["speaker"] = turns[-2]["speaker"]
            turns = turns[:-2] + [_make_turn(merged_words)]
            merged_count += 1
            changed = True

        if not changed:
            break

    if merged_count:
        print(f"      Fragmentation fix: merged {merged_count} micro-turns", flush=True)
    return turns


# ── Output ────────────────────────────────────────────────────────────────────
def save_outputs(turns: list, transcription_info: dict, audio_path: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stem      = Path(audio_path).stem
    txt_path  = os.path.join(OUTPUT_DIR, f"{stem}_diarized.txt")
    json_path = os.path.join(OUTPUT_DIR, f"{stem}_diarized.json")

    # ── Plain text ─────────────────────────────────────────────────────────────
    lines = []
    for t in turns:
        ts = f"[{fmt_time(t['start'])} --> {fmt_time(t['end'])}]"
        lines.append(f"{t['speaker']:12s} {ts}  {t['text']}")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── Rich JSON ──────────────────────────────────────────────────────────────
    speakers = sorted({t["speaker"] for t in turns})
    output = {
        "metadata": {
            "file":                 os.path.basename(audio_path),
            "duration_sec":         transcription_info["duration"],
            "language":             transcription_info["language"],
            "language_probability": transcription_info["language_probability"],
            "model":                transcription_info["model"],
            "speakers":             speakers,
            "num_turns":            len(turns),
            "total_words":          sum(t["word_count"] for t in turns),
            "processed_at":         datetime.now().isoformat(timespec="seconds"),
        },
        "turns": turns,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n Transcript : {txt_path}")
    print(f" JSON       : {json_path}")
    return txt_path, json_path


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not os.path.isfile(AUDIO_FILE):
        print(f"ERROR: Audio file not found: {AUDIO_FILE}")
        sys.exit(1)

    print("=" * 65, flush=True)
    print("  HIGH-ACCURACY TRANSCRIPTION + SPEAKER DIARIZATION", flush=True)
    print("=" * 65, flush=True)
    print(f"  File   : {AUDIO_FILE}", flush=True)
    print(f"  Model  : {WHISPER_MODEL}  |  Diarizer: pyannote-3.1  |  Device: {DEVICE}", flush=True)
    print("=" * 65, flush=True)

    # Load audio
    print("[0/3] Loading audio via PyAV ...", flush=True)
    audio_16k, orig_sr = load_audio(AUDIO_FILE, target_sr=WHISPER_SR)
    audio_mono         = audio_16k[0] if audio_16k.ndim > 1 else audio_16k
    print(f"      {len(audio_mono)/WHISPER_SR:.1f} sec | Orig SR: {orig_sr} Hz", flush=True)

    # 1. Transcribe → words with timestamps
    words, t_info = transcribe(audio_mono)

    # 2. Diarize — pass 16kHz audio so pyannote gets better speaker embeddings
    #    (avoid double-resampling from 8kHz; pyannote operates natively at 16kHz)
    speaker_segs = diarize(audio_16k, WHISPER_SR)

    # 3. Word-level alignment → turns
    print("[3/3] Word-level speaker alignment ...", flush=True)
    turns = build_turns(words, speaker_segs)
    turns = filter_hallucinations(turns)
    turns = fix_fragmented_turns(turns, sandwich_min_words=5, edge_min_words=3)
    print(f"      Turns after fixing: {len(turns)}", flush=True)

    # Save
    txt_path, json_path = save_outputs(turns, t_info, AUDIO_FILE)

    # Preview
    print("\n── Preview (first 12 turns) " + "─" * 36)
    for t in turns[:12]:
        ts = f"[{fmt_time(t['start'])} --> {fmt_time(t['end'])}]"
        preview = t["text"][:90] + ("…" if len(t["text"]) > 90 else "")
        print(f"  {t['speaker']:12s} {ts}  {preview}")
    print("─" * 65)
    print(f"Done.  {len(turns)} turns | {sum(t['word_count'] for t in turns)} words",
          flush=True)


if __name__ == "__main__":
    main()
