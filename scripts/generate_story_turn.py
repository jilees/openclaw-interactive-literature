#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib import request, error as urlerror

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT
ENGINE = REPO_ROOT / 'scripts' / 'story_engine.py'
RENDER = REPO_ROOT / 'scripts' / 'render_story_audio.sh'


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def engine_turn(story_id: str, user_input: str, no_log: bool = False) -> dict:
    cmd = [
        'python3', str(ENGINE), 'turn',
        '--story-id', story_id,
        '--user-input', user_input,
    ]
    if no_log:
        cmd.append('--no-log')
    out = run(cmd)
    return json.loads(out)


def engine_commit(story_id: str, scene_text: str, branch_note: str, state_patch: dict, model_route: str = ''):
    run([
        'python3', str(ENGINE), 'commit',
        '--story-id', story_id,
        '--scene-text', scene_text,
        '--branch-note', branch_note,
        '--state-patch', json.dumps(state_patch, ensure_ascii=False),
        '--model', model_route,
    ])


def resolve_model(selected: str) -> str:
    if selected != 'default':
        return selected
    # Story generation must run on GPT-5.4.
    return 'custom-api-openai-com/gpt-5.4'


def provider_from_model(model: str):
    # Supported routes for this script
    if model.startswith('openrouter/'):
        return {
            'provider': 'openrouter',
            'base_url': 'https://openrouter.ai/api/v1/chat/completions',
            'api_key': os.getenv('OPENROUTER_API_KEY', ''),
            'model': model.split('/', 1)[1],
            'headers': {
                'HTTP-Referer': 'https://openclaw.ai',
                'X-Title': 'interactive-literature',
            }
        }
    if model.startswith('custom-api-openai-com/'):
        return {
            'provider': 'openai',
            'base_url': 'https://api.openai.com/v1/chat/completions',
            'api_key': os.getenv('OPENAI_API_KEY', ''),
            'model': model.split('/', 1)[1],
            'headers': {}
        }
    if model.startswith('openai/'):
        return {
            'provider': 'openai',
            'base_url': 'https://api.openai.com/v1/chat/completions',
            'api_key': os.getenv('OPENAI_API_KEY', ''),
            'model': model.split('/', 1)[1],
            'headers': {}
        }
    raise RuntimeError(f'Unsupported model route for script: {model}')


def episode_ctx_path(story_id: str) -> Path:
    return ROOT / 'memory' / 'stories' / story_id / 'episode_context.json'


def load_episode_ctx(story_id: str) -> dict:
    p = episode_ctx_path(story_id)
    if not p.exists():
        return {'episode_index': 1, 'entries': [], 'completed': []}
    try:
        ctx = json.loads(p.read_text(encoding='utf-8'))
        ctx.setdefault('episode_index', 1)
        ctx.setdefault('entries', [])
        ctx.setdefault('completed', [])
        return ctx
    except Exception:
        return {'episode_index': 1, 'entries': [], 'completed': []}


def save_episode_ctx(story_id: str, ctx: dict):
    p = episode_ctx_path(story_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')


def story_settings_path(story_id: str) -> Path:
    return ROOT / 'memory' / 'stories' / story_id / 'settings.json'


def load_story_settings(story_id: str) -> dict:
    p = story_settings_path(story_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def should_render_audio(story_id: str) -> bool:
    # Per-story switch: settings.json {"audio_enabled": false}
    cfg = load_story_settings(story_id)
    val = cfg.get('audio_enabled', True)
    return bool(val)


def should_roll_episode(user_input: str) -> bool:
    u = (user_input or '').lower()
    if 'эпизод' not in u:
        return False
    keys = ('начина', 'запуска', 'сначала', 'старт')
    return any(k in u for k in keys)


def trim_words(text: str, max_words: int) -> str:
    words = (text or '').replace('\n', ' ').split()
    if len(words) <= max_words:
        return ' '.join(words)
    return ' '.join(words[:max_words]).rstrip(' ,.;:') + '…'


def estimate_words_150_budget(text: str) -> str:
    # Roughly ~150 tokens target for RU text.
    return trim_words(text, 115)


def resolve_summary_model() -> str:
    # Summary model default: GPT-5-mini (can be overridden via env).
    return os.getenv('STORY_SUMMARY_MODEL', 'custom-api-openai-com/gpt-5-mini')

def summarize_episode(entries: list) -> str:
    parts = [str(e.get('summary', '')).strip() for e in entries if str(e.get('summary', '')).strip()]
    if not parts:
        return ''
    # Keep 150-200 token-ish budget (~120-150 words) for whole episode summary.
    joined = ' '.join(parts)
    return trim_words(joined, 145)


def build_scene_summary(scene_text: str, user_input: str) -> str:
    model_route = resolve_summary_model()
    p = provider_from_model(model_route)
    if not p['api_key']:
        # Safe fallback if summary model key is missing.
        t = re.sub(r'\s+', ' ', (scene_text or '').strip())
        return estimate_words_150_budget(t)

    prompt = (
        'Выпиши факты хода списком для continuity интерактивной истории.\n'
        'Формат: каждый факт — отдельная строка, начинается с "- ".\n'
        'Правила:\n'
        '- Первая строка: решение пользователя — "- Решение: <кратко>".\n'
        '- Затем: конкретные события, открытия, изменения состояния из сцены.\n'
        '- Последняя строка: чем закончился ход (клиффхэнгер / точка остановки).\n'
        '- До 200 слов суммарно. Без литературных украшений.\n\n'
        f'РЕШЕНИЕ ПОЛЬЗОВАТЕЛЯ: {user_input}\n\n'
        f'СЦЕНА:\n{scene_text}'
    )
    messages = [
        {'role': 'system', 'content': 'Ты — технический редактор continuity интерактивной истории. Пиши по-русски.'},
        {'role': 'user', 'content': prompt}
    ]

    body = {
        'model': p['model'],
        'messages': messages,
    }
    if p.get('provider') == 'openai':
        body['max_completion_tokens'] = 3000
    else:
        body['max_tokens'] = 3000

    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = request.Request(p['base_url'], data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f"Bearer {p['api_key']}")
    for k, v in p['headers'].items():
        req.add_header(k, v)

    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode('utf-8')
        obj = json.loads(raw)
        content = obj['choices'][0]['message']['content']
        text = re.sub(r'\s+', ' ', str(content).strip())
        if text:
            return trim_words(text, 200)
    except Exception:
        pass

    # Last-resort fallback (deterministic trim)
    t = re.sub(r'\s+', ' ', (scene_text or '').strip())
    return estimate_words_150_budget(t)


def compact_packet(packet: dict, episode_ctx: dict) -> dict:
    state = packet.get('state', {})
    world = packet.get('world', {})
    chars = packet.get('characters', {})
    names = []
    for c in chars.get('characters', [])[:16]:
        if isinstance(c, dict):
            names.append({
                'name': c.get('name'),
                'role': c.get('role'),
                'status': c.get('status'),
            })

    ep = int(episode_ctx.get('episode_index', 1))
    entry_objs = [e for e in episode_ctx.get('entries', []) if int(e.get('episode', 1)) == ep]
    entries = [str(e.get('summary', '')).strip() for e in entry_objs if str(e.get('summary', '')).strip()]
    completed = [
        {
            'episode': int(e.get('episode', 0)),
            'summary': str(e.get('summary', '')).strip(),
        }
        for e in episode_ctx.get('completed', [])
        if str(e.get('summary', '')).strip()
    ]

    return {
        'story_id': packet.get('story_id'),
        'genre': packet.get('genre'),
        'premise': packet.get('premise'),
        'phase': state.get('phase'),
        'tension': state.get('tension'),
        'open_loops': state.get('open_loops', [])[:8],
        'resolved_loops': state.get('resolved_loops', [])[:8],
        'world_anchors': world.get('anchors', [])[:8],
        'world_rules': world.get('rules', [])[:12],
        'do_not_break': world.get('do_not_break', [])[:12],
        'characters': names,
        'episode_summaries': entries,
        'completed_episode_summaries': completed,
        'user_input': packet.get('user_input', ''),
    }


def build_messages(packet: dict, episode_ctx: dict):
    slim = compact_packet(packet, episode_ctx)
    add_branch = should_add_branch(packet.get('user_input', ''))
    system = (
        'Пиши литературную сцену интерактивной истории на русском языке. '
        'Соблюдай continuity, характеры и правила мира; держи стиль цельным и читаемым. '
                'Добавляй элементы хоррора постепенно и деликатно (нарастающий саспенс, тревожные детали среды), без резких жанровых скачков. '
        'Не пиши мета-комментарии и служебные пояснения. '
        'Если это обычный ход эпизода, добавь в конце блок "Развилка" с 2-4 вариантами действий героини без спойлеров. '
        'Если в запросе есть "без развилки" или это пролог/финал эпизода — без блока развилки.'
    )
    user = {
        'task': 'Сгенерируй следующий ход истории по данным.',
        'need_branch_block': add_branch,
        'format': '4-6 абзацев литературного текста; в конце (если need_branch_block=true): заголовок "Развилка" и нумерованные пункты 1)..N).',
        'input': slim,
    }
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}
    ]


def should_add_branch(user_input: str) -> bool:
    u = (user_input or '').lower()
    if 'пролог' in u:
        return False
    if 'последн' in u and 'эпизод' in u:
        return False
    if 'без развилки' in u:
        return False
    return True


def split_scene_and_branch(full_text: str):
    t = (full_text or '').strip()
    # Support headings like "Развилка" and markdown variants like "### Развилка"
    m = re.search(r'\n\n(?:#+\s*)?(?:\*\*\s*)?Развилка(?:\s*\*\*)?\s*\n', t)
    if not m:
        return t, ''
    i = m.start()
    return t[:i].rstrip(), t[i + 2:].strip()


def default_branch_block() -> str:
    return (
        'Развилка\n'
        '1) Сначала зафиксировать улики и закрыть риск потери данных, затем двигаться по следу.\n'
        '2) Идти по горячему следу немедленно, принимая риск неполной доказательной базы.\n'
        '3) Разделить контур: часть команды держит данные, часть ведёт перехват по живому каналу.\n'
        '4) Поднять уровень через президентский контур и выжать доступ к скрытым маршрутам без публичного шума.'
    )


def call_model(model_route: str, packet: dict, episode_ctx: dict, max_tokens: int = 1600) -> dict:
    p = provider_from_model(model_route)
    if not p['api_key']:
        raise RuntimeError(f"Missing API key for model route {model_route}")

    body = {
        'model': p['model'],
        'messages': build_messages(packet, episode_ctx),
    }
    # OpenAI GPT-5.x via Chat Completions expects max_completion_tokens.
    if p.get('provider') == 'openai':
        body['max_completion_tokens'] = max_tokens
    else:
        body['max_tokens'] = max_tokens

    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = request.Request(p['base_url'], data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f"Bearer {p['api_key']}")
    for k, v in p['headers'].items():
        req.add_header(k, v)

    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode('utf-8')
    except urlerror.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"HTTP {e.code} from model provider: {err_body}")

    obj = json.loads(raw)
    content = obj['choices'][0]['message']['content']
    text = str(content).strip()
    if not text:
        usage = obj.get('usage', {}) if isinstance(obj, dict) else {}
        raise RuntimeError(f"Model returned empty text. usage={usage}")
    return {'raw': obj, 'text': text}


def render_audio(scene_text: str, output_path: str):
    text_file = '/tmp/story_turn_text.txt'
    Path(text_file).write_text(scene_text, encoding='utf-8')
    run([
        'bash', str(RENDER),
        '--text-file', text_file,
        '--output', output_path,
    ])


def main():
    ap = argparse.ArgumentParser(description='Generate + commit one interactive literature turn with compact LLM input')
    ap.add_argument('--story-id', required=True)
    ap.add_argument('--user-input', required=True)
    ap.add_argument('--model', default='default', help='Model route (default|openrouter/...|custom-api-openai-com/...)')
    ap.add_argument('--max-tokens', type=int, default=1600)
    ap.add_argument('--audio-output', default='')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    turn = engine_turn(args.story_id, args.user_input, no_log=args.dry_run)
    packet = turn.get('generation_packet', {})
    if not packet:
        raise RuntimeError('No generation_packet from story_engine turn')

    model_route = resolve_model(args.model)

    ep_ctx = load_episode_ctx(args.story_id)
    if should_roll_episode(packet.get('user_input', '')) and not args.dry_run:
        cur_ep = int(ep_ctx.get('episode_index', 1))
        cur_entries = [e for e in ep_ctx.get('entries', []) if int(e.get('episode', 1)) == cur_ep]
        ep_sum = summarize_episode(cur_entries)
        if ep_sum:
            completed = ep_ctx.setdefault('completed', [])
            completed = [e for e in completed if int(e.get('episode', -1)) != cur_ep]
            completed.append({'episode': cur_ep, 'summary': ep_sum})
            ep_ctx['completed'] = sorted(completed, key=lambda x: int(x.get('episode', 0)))
        ep_ctx['episode_index'] = cur_ep + 1

    result = call_model(model_route, packet, ep_ctx, max_tokens=args.max_tokens)

    scene_text = result['text']
    if not scene_text:
        raise RuntimeError('Model returned empty scene text')

    narrative_text, branch_block = split_scene_and_branch(scene_text)
    if should_add_branch(packet.get('user_input', '')) and not branch_block:
        branch_block = default_branch_block()
        scene_text = narrative_text.rstrip() + '\n\n' + branch_block

    branch_note = (packet.get('user_input', '') or '').strip()[:220]
    state_patch = {}

    if not args.dry_run:
        engine_commit(args.story_id, scene_text, branch_note, state_patch, model_route=model_route)
        summary = build_scene_summary(narrative_text, packet.get('user_input', ''))
        ep = int(ep_ctx.get('episode_index', 1))
        ep_ctx.setdefault('entries', []).append({'episode': ep, 'summary': summary})
        save_episode_ctx(args.story_id, ep_ctx)

    audio_path = ''
    if args.audio_output and not args.dry_run and should_render_audio(args.story_id):
        # For audio, never include the branch options block.
        render_audio(narrative_text, args.audio_output)
        audio_path = args.audio_output

    usage = result['raw'].get('usage', {})
    current_ep = int(ep_ctx.get('episode_index', 1))
    ep_count = len([e for e in ep_ctx.get('entries', []) if int(e.get('episode', 1)) == current_ep])
    out = {
        'ok': True,
        'model': model_route,
        'usage': usage,
        'scene_text': scene_text,
        'narrative_text': narrative_text,
        'branch_block': branch_block,
        'branch_note': branch_note,
        'state_patch': state_patch,
        'audio_path': audio_path,
        'episode_index': current_ep,
        'episode_summaries_count': ep_count,
        'completed_episode_summaries_count': len(ep_ctx.get('completed', [])),
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
        sys.exit(1)
