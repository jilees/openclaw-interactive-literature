#!/usr/bin/env python3
"""One-shot repair script: regenerate episode summary and state arcs via LLM.

Usage:
    set -a && source .env && set +a && PYTHONUTF8=1 python scripts/repair_episode.py \
        --story-id everestown-incident --episode 2 --new-index 3
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_story_turn import (
    ROOT,
    load_episode_ctx,
    save_episode_ctx,
    build_episode_summary_llm,
    build_episode_state_patch,
    apply_state_patch_direct,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--story-id', required=True)
    ap.add_argument('--episode', type=int, required=True, help='Episode number to repair')
    ap.add_argument('--new-index', type=int, required=True, help='episode_index to set after repair')
    args = ap.parse_args()

    ep_ctx = load_episode_ctx(args.story_id)
    ep = args.episode

    entries = [e for e in ep_ctx.get('entries', []) if int(e.get('episode', 0)) == ep]
    if not entries:
        # Try completed entries as fallback (shouldn't happen normally)
        print(f'No entries found for episode {ep}', file=sys.stderr)
        sys.exit(1)

    print(f'Generating episode {ep} summary from {len(entries)} turn entries…', file=sys.stderr)
    ep_sum = build_episode_summary_llm(entries)
    if not ep_sum:
        print('ERROR: LLM returned empty summary', file=sys.stderr)
        sys.exit(1)

    print(f'Summary ({len(ep_sum.split())} words):\n{ep_sum}\n', file=sys.stderr)

    # Update completed
    completed = [e for e in ep_ctx.get('completed', []) if int(e.get('episode', -1)) != ep]
    completed.append({'episode': ep, 'summary': ep_sum})
    ep_ctx['completed'] = sorted(completed, key=lambda x: int(x.get('episode', 0)))

    # Clear entries for this episode
    ep_ctx['entries'] = [e for e in ep_ctx.get('entries', []) if int(e.get('episode', 0)) != ep]

    # Set new episode_index
    ep_ctx['episode_index'] = args.new_index

    save_episode_ctx(args.story_id, ep_ctx)
    print(f'episode_context.json updated. episode_index → {args.new_index}', file=sys.stderr)

    # Generate and apply state patch
    state_path = ROOT / 'memory' / 'stories' / args.story_id / 'state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    open_loops = state.get('open_loops', [])
    resolved_loops = state.get('resolved_loops', [])

    print('Generating state patch (open/resolved loops, tension, hook)…', file=sys.stderr)
    patch = build_episode_state_patch(ep_sum, open_loops, resolved_loops)
    if not patch:
        print('WARNING: state patch returned empty — state.json not modified', file=sys.stderr)
    else:
        hook = patch.pop('next_episode_hook', '')
        print(f'State patch: {json.dumps(patch, ensure_ascii=False)}', file=sys.stderr)
        if hook:
            print(f'Next episode hook: {hook}', file=sys.stderr)
            ep_ctx = load_episode_ctx(args.story_id)
            ep_ctx['next_episode_hook'] = hook
            save_episode_ctx(args.story_id, ep_ctx)
        apply_state_patch_direct(args.story_id, patch)
        print('state.json updated.', file=sys.stderr)

    print(json.dumps({'ok': True, 'episode': ep, 'new_index': args.new_index}, ensure_ascii=False))


if __name__ == '__main__':
    main()
