# AGENTS.md — Interactive Literature Repo Contract

Этот репозиторий — основной источник правды для story-mode.

## Структура

- `scripts/` — исполняемые скрипты генерации/коммита/аудио.
- `references/` — референсы (контракт истории, жанровые пресеты).
- `memory/stories/` — состояние историй, таймлайны, снапшоты.

## Основные правила

1. Генерация нового текста: **строго** `custom-api-openai-com/gpt-5.4`.
2. Fallback-подмена модели для основной генерации: **запрещена**.
3. Саммари хода: отдельный LLM-вызов на `custom-api-openai-com/gpt-5-mini`.
4. Саммари хранить компактно (цель: ~150 слов), с фиксацией концовки хода.
5. Story audio может быть отключено per-story через `memory/stories/<story_id>/settings.json` (`audio_enabled=false`).
6. Любые user-facing отчёты и ключевые изменения должны опираться на фактический JSON/логи скриптов.

## Скрипты

- `scripts/story_engine.py` — init/turn/commit/status.
- `scripts/generate_story_turn.py` — единый pipeline генерации (model call + commit + summary + optional audio).
- `scripts/render_story_audio.sh` — рендер narration audio.

## Операционная дисциплина

- Перед массовыми изменениями сохранять снапшот состояния истории.
- При изменении формата/контракта — обновлять этот файл и фиксировать в git.
- Не удалять рабочую структуру до подтверждения миграции.
