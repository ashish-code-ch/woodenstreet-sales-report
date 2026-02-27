#!/bin/bash
#!/usr/bin/env bash
# Whisper batch runner - multiple audio files -> one text file each
# Digpal Project
set -euo pipefail
shopt -s nullglob

source "/c/venvs/whisperx/Scripts/activate"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8


UPLOAD_DIR="/c/Users/Ashish Mehta/Documents/ASR/Whisper/digpal/audio_files"
OUTPUT_DIR="/c/Users/Ashish Mehta/Documents/ASR/Whisper/digpal/converted_files"
CACHE_DIR="/c/Users/Ashish Mehta/Documents/ASR/Whisper/digpal/.cache"
MODEL="large-v3"   # More accurate, slower
LOG_FILE="/c/Users/Ashish Mehta/Documents/ASR/Whisper/digpal/cron_log_new.txt"
RUNNER="/c/Users/Ashish Mehta/Documents/ASR/Whisper/digpal/transcribe_fw.py"

# Create output + cache dir if missing
mkdir -p "$OUTPUT_DIR"
mkdir -p "$CACHE_DIR"
DONE_DIR="/c/Users/Ashish Mehta/Documents/ASR/Whisper/digpal/Done_files"
mkdir -p "$DONE_DIR"

# Set Whisper cache location
export XDG_CACHE_HOME="$CACHE_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cron started" >> "$LOG_FILE"

# Loop through all supported audio files
for audio in "$UPLOAD_DIR"/*.{mp3,wav,m4a,flac}; do
    [ -e "$audio" ] || continue   # skip if no file found

    base=$(basename "$audio")
    name="${base%.*}"
    output_txt="$OUTPUT_DIR/$name.txt"

    if [ -f "$output_txt" ]; then
        echo "Skipping (already transcribed): $base" >> "$LOG_FILE"
        continue
    fi

#--language English \
#--compression_ration_threshold 2.4 \

    echo " Transcribing: $base" >> "$LOG_FILE"
    python -X utf8 -m whisper "$audio" \
        --model "$MODEL" \
        --task transcribe \
        --word_timestamps True \
        --initial_prompt "This is a customer support call about furniture orders at WoodenStreet." \
        --beam_size 5 \
        --patience 1 \
        --condition_on_previous_text False \
        --compression_ratio_threshold 2.4 \
        --logprob_threshold -1.0 \
        --no_speech_threshold 0.6 \
        --output_dir "$OUTPUT_DIR" --output_format txt \
        --fp16 False \
        --verbose False >> "$LOG_FILE" 2>&1

    # Check if txt file created
    if [ -f "$output_txt" ]; then
        echo " Done: $output_txt" >> "$LOG_FILE"
        # rm -f "$audio"   # delete audio after success
       # echo " Deleted: $base" >> "$LOG_FILE"
       mv -f -- "$audio" "$DONE_DIR/"
       echo " Moved: $base -> $DONE_DIR" >> "$LOG_FILE"
    else
        echo " Failed: $base" >> "$LOG_FILE"
    fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')]  Cron finished" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
