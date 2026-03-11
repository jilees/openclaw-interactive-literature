# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Python + Bash interactive literature engine for OpenClaw. Stories are generated in Russian using an LLM pipeline, with optional audio narration via ElevenLabs.

## Running Scripts

```bash
# Full generation pipeline (main entry point)
set -a && source .env && set +a && PYTHONUTF8=1 python scripts/generate_story_turn.py \
  --story-id "my-story" --user-input "действие пользователя" 2>&1

# Individual engine commands (for debugging/inspection)
PYTHONUTF8=1 python scripts/story_engine.py init --story-id "my-story" --genre "sci-fi" --premise "..."
PYTHONUTF8=1 python scripts/story_engine.py status --story-id "my-story"
```

**Runtime dependencies:** Python 3, `curl`, `ffmpeg`, `ffprobe`
**Required env vars:** `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` (audio only)

`source .env` is required before calling generate scripts — they do not call `load_dotenv`.
`PYTHONUTF8=1` is required on Windows for correct Cyrillic output.

No build step, no package manager, no test suite.

## Architecture

### Core scripts

- **[scripts/story_engine.py](scripts/story_engine.py)** — State manager. Handles story directory layout, JSON persistence (atomic `.tmp` writes), input validation against world rules, and image quota tracking. Four subcommands: `init`, `turn`, `commit`, `status`.

- **[scripts/generate_story_turn.py](scripts/generate_story_turn.py)** — Main pipeline. Compacts story state, calls the LLM, parses JSON output, commits state, generates episode summary, and optionally triggers audio. Manages episode rolling when user triggers a new episode.

- **[scripts/render_story_audio.sh](scripts/render_story_audio.sh)** — Audio renderer. Calls ElevenLabs TTS (George voice), strips the "Развилка" block, then mixes narration with a random BGM track from `scripts/audio_assets/mixkit/` using ffmpeg. Output is Opus 48kHz mono.

### Story state (per story in `memory/stories/<story_id>/`)

| File | Purpose |
|---|---|
| `state.json` | Narrative phase, tension (0..1), open/resolved loops |
| `world.json` | World rules and do_not_break anchors |
| `characters.json` | Cast with roles and status |
| `episode_context.json` | Episode index and per-episode summaries (target: ~150 words RU) |
| `images.json` | Daily image quota tracking |
| `timeline.jsonl` | Append-only event log |
| `settings.json` | Per-story config — notably `audio_enabled` |
| `snapshots/` | Timestamped backups before major operations |

### LLM output contract

The model must return JSON matching [references/story-contract.md](references/story-contract.md):

```json
{
  "scene_text": "4-6 абзацев литературного текста на русском",
  "continuity_notes": ["..."],
  "branch_note": "...",
  "state_patch": { "tension": 0.55, "open_loops": [], "resolved_loops": [] },
  "suggested_directions": ["...", "...", "..."],
  "image_prompt_en": "cinematic illustration prompt (optional)"
}
```

Genre tone/pacing rules are in [references/genre-presets.md](references/genre-presets.md).

## Operational Rules (from AGENTS.md)

- **Primary model:** `custom-api-openai-com/gpt-5.4` — model substitution for main generation is **prohibited**.
- **Summary model:** `custom-api-openai-com/gpt-5-mini`.
- Audio can be toggled per story via `settings.json` (`audio_enabled: false`).
- Take a snapshot before any mass state changes.
- When changing the output format or contract, update [AGENTS.md](AGENTS.md) and commit.
- All user-facing reports must be derived from actual JSON/log files, not assumptions.
