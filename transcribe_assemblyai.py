#!/usr/bin/env python3
"""
Audio transcription + speaker diarization via AssemblyAI cloud API.
  - Uploads audio, gets back speaker-labeled transcript with word timestamps
  - No local GPU/model needed
  - Very high accuracy; supports low-quality phone audio well

Setup:
  1. Create free account at https://www.assemblyai.com/
  2. Get API key from https://www.assemblyai.com/app/account
  3. Save key to: /home/user/Documents/AI/SalesScorecard/assemblyai_key.txt
  4. pip install assemblyai  (already done if you ran the installer)
"""

import os, sys, json
from pathlib import Path
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────────────────────────────
AUDIO_FILE  = "/home/user/Documents/AI/SalesScorecard/recording/20250905-185051_919151117439_3245-all.mp3"
OUTPUT_DIR  = "/home/user/Documents/AI/SalesScorecard/transcripts"
KEY_FILE    = "/home/user/Documents/AI/SalesScorecard/assemblyai_key"
# ─────────────────────────────────────────────────────────────────────────────


def fmt_time(ms: int) -> str:
    """Convert milliseconds to HH:MM:SS.mmm"""
    s_total = ms / 1000
    h, rem  = divmod(int(s_total), 3600)
    m, s    = divmod(rem, 60)
    msec    = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"


def load_api_key() -> str:
    if not os.path.isfile(KEY_FILE):
        print(f"ERROR: AssemblyAI key file not found: {KEY_FILE}")
        print("  Create it with your API key (get one free at https://www.assemblyai.com/app/account)")
        sys.exit(1)
    key = open(KEY_FILE).read().strip()
    if not key:
        print("ERROR: Key file is empty.")
        sys.exit(1)
    return key


def _upsample_to_wav(audio_path: str, target_sr: int = 16000) -> str:
    """Convert any audio to 16kHz mono WAV (better AssemblyAI accuracy)."""
    import av, torch, torchaudio.functional as F, numpy as np, wave, struct

    container = av.open(audio_path)
    stream    = container.streams.audio[0]
    orig_sr   = stream.sample_rate

    chunks = []
    for frame in container.decode(audio=0):
        arr = frame.to_ndarray()
        if arr.ndim == 1:
            arr = arr[np.newaxis, :]
        chunks.append(arr.astype(np.float32))
    audio = np.concatenate(chunks, axis=1)

    if orig_sr != target_sr:
        audio = F.resample(torch.from_numpy(audio), orig_sr, target_sr).numpy()

    mono = audio.mean(axis=0) if audio.shape[0] > 1 else audio[0]
    mono = np.clip(mono, -1.0, 1.0)
    pcm  = (mono * 32767).astype(np.int16)

    out_path = audio_path.rsplit(".", 1)[0] + "_16k.wav"
    with wave.open(out_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        wf.writeframes(pcm.tobytes())

    print(f"      Upsampled to 16kHz WAV: {os.path.basename(out_path)}", flush=True)
    return out_path


def transcribe_assemblyai(audio_path: str):
    """Upload audio to AssemblyAI and return speaker-labeled utterances."""
    import assemblyai as aai

    aai.settings.api_key = load_api_key()

    # Pre-upsample audio to 16kHz WAV for better AssemblyAI accuracy
    audio_path = _upsample_to_wav(audio_path)

    config = aai.TranscriptionConfig(
        speech_models        = ["universal-3-pro", "universal-2"],  # v3-pro for quality + v2 for Hindi
        speaker_labels       = True,
        language_detection   = True,             # auto-detect Hindi/English mix
        punctuate            = True,
        format_text          = True,
        disfluencies         = False,
        filter_profanity     = False,
    )

    print(f"[1/2] Uploading and transcribing: {os.path.basename(audio_path)} ...", flush=True)
    print("      (AssemblyAI processes in the cloud — usually 1-3 min)", flush=True)

    transcriber = aai.Transcriber()
    transcript  = transcriber.transcribe(audio_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        print(f"ERROR: {transcript.error}")
        sys.exit(1)

    print(f"      Status: {transcript.status}", flush=True)
    print(f"      Utterances: {len(transcript.utterances)}", flush=True)
    return transcript


def build_output(transcript, audio_path: str):
    """Convert AssemblyAI transcript to our standard turn/word format."""
    turns = []
    for utt in transcript.utterances:
        words = []
        if utt.words:
            for w in utt.words:
                words.append({
                    "word":       w.text,
                    "start":      round(w.start / 1000, 3),   # ms → sec
                    "end":        round(w.end   / 1000, 3),
                    "confidence": round(w.confidence, 3),
                    "speaker":    w.speaker,
                })

        turns.append({
            "speaker":        f"SPEAKER_{utt.speaker}",
            "start":          round(utt.start / 1000, 3),
            "end":            round(utt.end   / 1000, 3),
            "duration":       round((utt.end - utt.start) / 1000, 3),
            "text":           utt.text,
            "word_count":     len(words),
            "avg_confidence": round(utt.confidence, 3) if utt.confidence else None,
            "words":          words,
        })

    speakers = sorted({t["speaker"] for t in turns})
    dur_sec  = round(transcript.audio_duration, 3) if transcript.audio_duration else None

    output = {
        "metadata": {
            "file":                 os.path.basename(audio_path),
            "duration_sec":         dur_sec,
            "language":             transcript.language_code,
            "model":                "assemblyai/best",
            "speakers":             speakers,
            "num_turns":            len(turns),
            "total_words":          sum(t["word_count"] for t in turns),
            "transcript_id":        transcript.id,
            "processed_at":         datetime.now().isoformat(timespec="seconds"),
        },
        "turns": turns,
    }
    return output


def save_outputs(output: dict, audio_path: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stem      = Path(audio_path).stem
    txt_path  = os.path.join(OUTPUT_DIR, f"{stem}_assemblyai.txt")
    json_path = os.path.join(OUTPUT_DIR, f"{stem}_assemblyai.json")

    # Plain text
    lines = []
    for t in output["turns"]:
        ts = f"[{fmt_time(int(t['start']*1000))} --> {fmt_time(int(t['end']*1000))}]"
        lines.append(f"{t['speaker']:12s} {ts}  {t['text']}")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Rich JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n Transcript : {txt_path}")
    print(f" JSON       : {json_path}")
    return txt_path, json_path


def main():
    if not os.path.isfile(AUDIO_FILE):
        print(f"ERROR: Audio file not found: {AUDIO_FILE}")
        sys.exit(1)

    print("=" * 65, flush=True)
    print("  ASSEMBLYAI TRANSCRIPTION + SPEAKER DIARIZATION", flush=True)
    print("=" * 65, flush=True)
    print(f"  File : {AUDIO_FILE}", flush=True)
    print("=" * 65, flush=True)

    transcript = transcribe_assemblyai(AUDIO_FILE)

    print("[2/2] Building output ...", flush=True)
    output = build_output(transcript, AUDIO_FILE)

    txt_path, json_path = save_outputs(output, AUDIO_FILE)

    # Preview
    turns = output["turns"]
    print("\n── Preview (first 12 turns) " + "─" * 36)
    for t in turns[:12]:
        ts      = f"[{fmt_time(int(t['start']*1000))} --> {fmt_time(int(t['end']*1000))}]"
        preview = t["text"][:90] + ("…" if len(t["text"]) > 90 else "")
        print(f"  {t['speaker']:12s} {ts}  {preview}")
    print("─" * 65)
    m = output["metadata"]
    print(f"Done.  {m['num_turns']} turns | {m['total_words']} words | "
          f"{m['duration_sec']}s | ID: {m['transcript_id']}", flush=True)


if __name__ == "__main__":
    main()
