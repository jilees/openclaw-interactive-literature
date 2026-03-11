#!/usr/bin/env python3
import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'memory' / 'stories'


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r'[^a-z0-9а-яё\-\s]+', '', s)
    s = re.sub(r'\s+', '-', s)
    return s.strip('-') or 'story'


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_jsonl(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')


def story_dir(story_id: str) -> Path:
    return BASE / slugify(story_id)


def read_story(story_id: str):
    d = story_dir(story_id)
    return {
        'dir': str(d),
        'state': load_json(d / 'state.json', {}),
        'world': load_json(d / 'world.json', {}),
        'characters': load_json(d / 'characters.json', {'characters': []}),
        'branches': load_json(d / 'branches.json', {'notes': []}),
        # images.json migration notes:
        # - legacy: daily/history (counted attempts or generated images)
        # - current: daily_generated + daily_delivered + history + sent_history
        'images': load_json(d / 'images.json', {
            'daily_generated': {},
            'daily_delivered': {},
            'history': [],
            'sent_history': [],
        }),
    }


def cmd_init(args):
    sid = slugify(args.story_id)
    d = story_dir(sid)
    d.mkdir(parents=True, exist_ok=True)

    state = {
        'story_id': sid,
        'genre': args.genre,
        'premise': args.premise,
        'language': 'ru',
        'style': 'literary',
        'phase': 'setup',
        'tension': 0.2,
        'open_loops': [],
        'resolved_loops': [],
        'created_at': now_iso(),
        'updated_at': now_iso(),
    }
    world = {
        'rules': [],
        'anchors': [args.premise],
        'do_not_break': [],
        'updated_at': now_iso(),
    }
    characters = {'characters': [], 'updated_at': now_iso()}
    branches = {'notes': [], 'updated_at': now_iso()}
    images = {'daily': {}, 'history': [], 'updated_at': now_iso()}

    save_json(d / 'state.json', state)
    save_json(d / 'world.json', world)
    save_json(d / 'characters.json', characters)
    save_json(d / 'branches.json', branches)
    save_json(d / 'images.json', images)

    append_jsonl(d / 'timeline.jsonl', {
        'at': now_iso(),
        'event': 'init',
        'genre': args.genre,
        'premise': args.premise,
    })

    print(json.dumps({'ok': True, 'story_id': sid, 'dir': str(d)}, ensure_ascii=False))


def image_hint(images: dict, user_input: str) -> dict:
    today = datetime.now().strftime('%Y-%m-%d')
    # Delivery-based quota (what user actually received in chat)
    delivered = int(images.get('daily_delivered', {}).get(today, 0))
    # Legacy/generated counter kept only for diagnostics
    generated = int(images.get('daily_generated', {}).get(today, images.get('daily', {}).get(today, 0)))

    key_words = ['кульминац', 'дуэль', 'погон', 'взрыв', 'портал', 'поцел', 'смерт', 'разоблач']
    matched = any(k in user_input.lower() for k in key_words)
    return {
        'today': today,
        'used': delivered,
        'generated_today': generated,
        'limit': 3,
        'should_generate': matched and delivered < 3,
        'reason': 'keyword-key-moment' if matched else 'no-keyword',
    }


def validate_input(story: dict, user_input: str) -> dict:
    warnings = []
    world = story.get('world', {})
    rules_blob = ' '.join(world.get('rules', []) + world.get('do_not_break', []))
    if rules_blob:
        # Lightweight heuristic placeholder.
        banned = [x.strip() for x in re.split(r'[;,]\s*', rules_blob) if x.strip()]
        for b in banned[:8]:
            if b and b.lower() in user_input.lower():
                warnings.append(f'Проверь соответствие правилу мира: {b}')

    return {
        'ok': len(warnings) == 0,
        'warnings': warnings,
        'suggest': [
            'Сместить действие в рамки действующих правил мира.',
            'Сохранить намерение героя, но изменить способ достижения цели.',
            'Сделать промежуточную сцену, объясняющую новый поворот без ломки канона.'
        ] if warnings else []
    }


def cmd_turn(args):
    story = read_story(args.story_id)
    state = story['state']
    if not state:
        raise SystemExit('Story not initialized. Run init first.')

    packet = {
        'story_id': state.get('story_id'),
        'genre': state.get('genre'),
        'language': 'ru',
        'style': 'literary',
        'target_scene': '4-6 paragraphs',
        'premise': state.get('premise'),
        'state': {
            'phase': state.get('phase'),
            'tension': state.get('tension', 0.2),
            'open_loops': state.get('open_loops', []),
            'resolved_loops': state.get('resolved_loops', []),
        },
        'world': story['world'],
        'characters': story['characters'],
        'branch_notes': story['branches'].get('notes', [])[-8:],
        'user_input': args.user_input,
        'contract_ref': str(ROOT / 'references' / 'story-contract.md'),
    }

    out = {
        'ok': True,
        'generation_packet': packet,
        'validator': validate_input(story, args.user_input),
        'image_hint': image_hint(story['images'], args.user_input),
    }

    if not args.no_log:
        append_jsonl(Path(story['dir']) / 'timeline.jsonl', {
            'at': now_iso(),
            'event': 'user_turn',
            'input': args.user_input,
            'validator_ok': out['validator']['ok'],
        })

    print(json.dumps(out, ensure_ascii=False))


def cmd_commit(args):
    story = read_story(args.story_id)
    state = story['state']
    if not state:
        raise SystemExit('Story not initialized. Run init first.')

    patch = {}
    if args.state_patch:
        patch = json.loads(args.state_patch)

    for k, v in patch.items():
        state[k] = v

    state['phase'] = 'in_progress'
    state['updated_at'] = now_iso()
    save_json(Path(story['dir']) / 'state.json', state)

    branches = story['branches']
    if args.branch_note:
        branches.setdefault('notes', []).append({
            'at': now_iso(),
            'note': args.branch_note,
        })
        branches['updated_at'] = now_iso()
        save_json(Path(story['dir']) / 'branches.json', branches)

    append_jsonl(Path(story['dir']) / 'timeline.jsonl', {
        'at': now_iso(),
        'event': 'commit',
        'branch_note': args.branch_note,
        'state_patch': patch,
        'model': (args.model or '').strip(),
        'scene_preview': (args.scene_text or '')[:500],
    })

    print(json.dumps({'ok': True, 'story_id': state.get('story_id')}, ensure_ascii=False))


def cmd_status(args):
    story = read_story(args.story_id)
    state = story['state']
    if not state:
        print(json.dumps({'ok': False, 'error': 'not_initialized'}, ensure_ascii=False))
        return

    d = Path(story['dir'])
    timeline_tail = []
    tl = d / 'timeline.jsonl'
    if tl.exists():
        lines = tl.read_text(encoding='utf-8').strip().splitlines()
        for line in lines[-5:]:
            try:
                timeline_tail.append(json.loads(line))
            except Exception:
                pass

    print(json.dumps({
        'ok': True,
        'story_id': state.get('story_id'),
        'genre': state.get('genre'),
        'phase': state.get('phase'),
        'tension': state.get('tension'),
        'open_loops': state.get('open_loops', []),
        'dir': story['dir'],
        'timeline_tail': timeline_tail,
    }, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description='Interactive literature story engine helper')
    sub = p.add_subparsers(dest='cmd', required=True)

    p_init = sub.add_parser('init')
    p_init.add_argument('--story-id', required=True)
    p_init.add_argument('--genre', default='mixed')
    p_init.add_argument('--premise', required=True)
    p_init.set_defaults(func=cmd_init)

    p_turn = sub.add_parser('turn')
    p_turn.add_argument('--story-id', required=True)
    p_turn.add_argument('--user-input', required=True)
    p_turn.add_argument('--no-log', action='store_true', help='Do not append user_turn event to timeline')
    p_turn.set_defaults(func=cmd_turn)

    p_commit = sub.add_parser('commit')
    p_commit.add_argument('--story-id', required=True)
    p_commit.add_argument('--scene-text', default='')
    p_commit.add_argument('--branch-note', default='')
    p_commit.add_argument('--state-patch', default='')
    p_commit.add_argument('--model', default='')
    p_commit.set_defaults(func=cmd_commit)

    p_status = sub.add_parser('status')
    p_status.add_argument('--story-id', required=True)
    p_status.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
