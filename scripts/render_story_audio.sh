#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Render Telegram-ready narration audio for story scene via ElevenLabs + lounge-random BGM.
# Defaults are tuned for current approved style:
# - voice: George storyteller (JBFqnCBsd6RMkjVDRZzb)
# - model: eleven_multilingual_v2
# - bgm: random track from scripts/audio_assets/mixkit/*.wav, no immediate repeats

TEXT=""
TEXT_FILE=""
OUTPUT=""
VOICE_ID="JBFqnCBsd6RMkjVDRZzb" # George storyteller
MODEL_ID="eleven_multilingual_v2"
STATE_FILE="${REPO_ROOT}/memory/audio_bgm_state.json"
WORKDIR="/tmp/story-audio-$$"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --text) TEXT="$2"; shift 2 ;;
    --text-file) TEXT_FILE="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --voice-id) VOICE_ID="$2"; shift 2 ;;
    --model-id) MODEL_ID="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$OUTPUT" ]]; then
  echo "--output is required" >&2
  exit 1
fi

if [[ -n "$TEXT_FILE" ]]; then
  TEXT="$(cat "$TEXT_FILE")"
fi

if [[ -z "$TEXT" ]]; then
  echo "Provide --text or --text-file" >&2
  exit 1
fi

# Keep branching options in text-only channel.
# For audio narration, strip everything from "Развилка" header to the end.
TEXT="$(python3 - <<'PY' "$TEXT"
import re,sys
text=sys.argv[1]
text=re.split(r'(?im)^\s*\*{0,2}\s*развилка\s*\*{0,2}\s*$', text, maxsplit=1)[0].rstrip()
print(text)
PY
)"

if [[ -z "${ELEVENLABS_API_KEY:-}" ]]; then
  echo "ELEVENLABS_API_KEY is not set" >&2
  exit 1
fi

mkdir -p "$WORKDIR" "$(dirname "$OUTPUT")"

PAYLOAD_JSON="$WORKDIR/payload.json"
VOICE_MP3="$WORKDIR/voice.mp3"
VOICE_WAV="$WORKDIR/voice.wav"
RAW_MIX_WAV="$WORKDIR/mix.wav"

python3 - <<'PY' "$PAYLOAD_JSON" "$TEXT" "$MODEL_ID"
import json,sys
out,text,model=sys.argv[1:4]
payload={
  "text": text,
  "model_id": model,
  "voice_settings": {"stability": 0.45, "similarity_boost": 0.75}
}
with open(out,'w',encoding='utf-8') as f:
  json.dump(payload,f,ensure_ascii=False)
PY

HTTP_CODE="$(curl -sS -o "$VOICE_MP3" -w "%{http_code}" \
  -X POST "https://api.elevenlabs.io/v1/text-to-speech/${VOICE_ID}" \
  -H "xi-api-key: ${ELEVENLABS_API_KEY}" \
  -H "Content-Type: application/json" \
  --data-binary "@${PAYLOAD_JSON}")"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ElevenLabs TTS failed: HTTP $HTTP_CODE" >&2
  head -c 500 "$VOICE_MP3" >&2 || true
  exit 1
fi

ffmpeg -y -i "$VOICE_MP3" -ar 48000 -ac 1 -c:a pcm_s16le "$VOICE_WAV" >/dev/null 2>&1

BGM_FILE="$(python3 - <<'PY' "$STATE_FILE" "$REPO_ROOT"
import glob, json, os, random, sys
from pathlib import Path
state_path = sys.argv[1]
repo = Path(sys.argv[2])
candidates = sorted(glob.glob(str(repo / 'scripts' / 'audio_assets' / 'mixkit' / 'mixkit-*.wav')))
if not candidates:
  candidates = sorted(glob.glob(str(repo / 'scripts' / 'audio_assets' / 'mixkit' / 'mixkit-*.mp3')))
if not candidates:
  print('')
  raise SystemExit(0)
last = None
if os.path.exists(state_path):
  try:
    with open(state_path, 'r', encoding='utf-8') as f:
      last = (json.load(f) or {}).get('last_bgm')
  except Exception:
    pass
pool = [x for x in candidates if x != last] or candidates
chosen = random.choice(pool)
os.makedirs(os.path.dirname(state_path), exist_ok=True)
with open(state_path, 'w', encoding='utf-8') as f:
  json.dump({'last_bgm': chosen}, f, ensure_ascii=False)
print(chosen)
PY
)"

if [[ -n "$BGM_FILE" && -f "$BGM_FILE" ]]; then
  VOICE_DUR="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VOICE_WAV" 2>/dev/null || echo 0)"
  BGM_DUR="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$BGM_FILE" 2>/dev/null || echo 0)"
  BGM_OFFSET="$(python3 - <<'PY' "$VOICE_DUR" "$BGM_DUR"
import random,sys
v=float(sys.argv[1] or 0)
b=float(sys.argv[2] or 0)
max_off=max(0.0,b-v-0.5)
print(f"{random.uniform(0,max_off):.3f}" if max_off>0 else "0")
PY
)"

  ffmpeg -y \
    -i "$VOICE_WAV" \
    -stream_loop -1 -ss "$BGM_OFFSET" -i "$BGM_FILE" \
    -filter_complex "[0:a]aresample=48000,volume=1.0,adelay=2000,apad=pad_dur=2[voice];[1:a]aresample=48000,volume=0.36[bgm];[voice][bgm]amix=inputs=2:duration=first:normalize=0[mix]" \
    -map "[mix]" -c:a pcm_s16le "$RAW_MIX_WAV" >/dev/null 2>&1
else
  cp "$VOICE_WAV" "$RAW_MIX_WAV"
fi

ffmpeg -y -i "$RAW_MIX_WAV" -c:a libopus -b:a 48k -ac 1 -ar 48000 "$OUTPUT" >/dev/null 2>&1

echo "OK: $OUTPUT"
rm -rf "$WORKDIR"
