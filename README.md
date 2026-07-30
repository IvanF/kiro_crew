# 🎼 AI Crew — Команда нейросетевых агентов

**AI Crew** — это оркестрованная команда специализированных AI-агентов на базе `kiro_cli`, которая автоматически проектирует, реализует, тестирует и рецензирует код по текстовому описанию задачи.

---

## Архитектура

```
Пользователь
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│                    CONDUCTOR (дирижёр)                  │
│  Изолированный оркестратор. Не пишет код сам.           │
│  Управляет состоянием, маршрутизирует сообщения.        │
│  Принимает решение о force-approve при лимите итераций. │
└──────────────┬──────────────────────────────────────────┘
               │
   ┌───────────▼───────────┐
   │     ARCHITECT         │  kiro_planner
   │  Анализирует промт.   │  → Выдаёт архитектуру + список задач для кодера
   └───────────┬───────────┘
               │ tasks[]  (topological sort → волны)
      ┌────────▼─────────────────────────────────────────┐
      │          для каждой задачи (по волнам)            │
      │                                                   │
      │   ┌──────────┐    ┌──────────┐    ┌──────────┐   │
      │   │  CODER   │───▶│  TESTER  │───▶│  CRITIC  │   │
      │   │ kiro_def │    │ kiro_def │    │ kiro_def │   │
      │   └──────────┘    └──────────┘    └────┬─────┘   │
      │        ▲                               │          │
      │        │    NEEDS_REWORK + инструкции  │          │
      │        └───────────────────────────────┘          │
      │             (до max_iterations раз)               │
      │                         │ APPROVED                │
      └─────────────────────────▼────────────────────────┘
                           следующая задача / DONE
```

### Компоненты

| Агент | Роль kiro | Задача |
|-------|-----------|--------|
| **Architect** | `kiro_planner` | Проектирует систему, делит на атомарные задачи |
| **Coder** | `kiro_default` | Реализует одну задачу: полные файлы, типы, docstrings |
| **Tester** | `kiro_default` | Пишет ≥20 тест-кейсов, запускает их, отчитывается |
| **Critic** | `kiro_default` | Сравнивает с требованием → `APPROVED` / `NEEDS_REWORK` |
| **Conductor** | — (Python) | State machine, маршрутизация, force-approve, retry |

---

## Flow

```
INIT
  └─▶ ARCHITECT
        └─▶ [Волна 1: задачи без зависимостей]
              └─▶ CODER (с retry при ошибке JSON-парсинга)
                    └─▶ TESTER (видит context_files предыдущих задач)
                          └─▶ CRITIC (получает реальный результат тестов)
                                ├─ APPROVED ──▶ следующая задача
                                └─ NEEDS_REWORK ──▶ CODER (итерация N+1)
                                       │
                                       ▼  (итерация == max_iterations)
                                 CONDUCTOR force-approves → следующая задача
        └─▶ [Волна 2 ...] ...
  └─▶ DONE — финальный отчёт
```

**Ключевые свойства:**
- Задачи с зависимостями выполняются после зависимых (topological sort → волны)
- Тестировщик получает `context_files` — все ранее реализованные файлы — для корректного мокирования зависимостей
- Критик получает реальный результат запуска тестов (`exit_code`, `stdout`, `passed`)
- При `NEEDS_REWORK` Критик передаёт конкретные инструкции кодеру
- **Force-approve** при достижении лимита итераций принимается **кондуктором** (не критиком), отражается в финальном отчёте
- При невалидном JSON-ответе кодера — автоматический retry с инструкцией по формату (до 2 раз, без траты итерации)
- Промты Coder и Critic содержат явное требование согласованности имён свойств (`_prefix` для приватных полей)

---

## Структура проекта

```
ai_crew/
├── main.py                    # Точка входа, CLI
├── config.yaml                # Конфигурация (таймауты, итерации)
├── requirements.txt
│
├── prompts/                   # Prompt-шаблоны агентов
│   ├── architect.md           # → JSON: architecture + tasks[]
│   ├── coder.md               # → JSON: task_id + files[] (+ naming rules)
│   ├── tester.md              # → JSON: test_files[] + findings[] (+ context_files)
│   └── critic.md              # → JSON: verdict + issues[] (+ naming check)
│
├── src/
│   ├── agents/
│   │   ├── base.py            # Абстрактный BaseAgent
│   │   ├── architect.py       # ArchitectAgent
│   │   ├── coder.py           # CoderAgent
│   │   ├── tester.py          # TesterAgent (принимает context_files)
│   │   └── critic.py          # CriticAgent (принимает test_run_output)
│   ├── conductor/
│   │   └── conductor.py       # Conductor: flow, force-approve, parse-retry
│   └── utils/
│       ├── kiro_runner.py     # Обёртка kiro CLI + JSON-парсер
│       ├── prompt_loader.py   # Загрузка шаблонов с подстановкой переменных
│       └── session_logger.py  # Лог сессии (JSON)
│
└── output/
    ├── sessions/
    │   └── {session_id}.json  # Полный лог сессии
    └── {session_id}/          # Артефакты сессии
        ├── architecture.json
        ├── coder_task_N.json
        ├── tester_task_N.json
        ├── critic_task_N_iter_M.json
        └── final_report.json
```

---

## Установка

```bash
# 1. Убедитесь, что kiro CLI установлен
kiro --version

# 2. Установите зависимости Python
pip install -r requirements.txt

# 3. Проверьте импорты
python -c "from src.conductor.conductor import Conductor; print('OK')"
```

---

## Использование

### Простой запуск

```bash
python main.py "Создай REST API для управления задачами: создание, удаление, обновление, список"
```

### Из файла

```bash
echo "Разработай парсер CSV с валидацией схемы и экспортом в JSON" > task.txt
python main.py --requirement-file task.txt
```

### Интерактивный режим

```bash
python main.py --interactive
```

### С сохранением отчёта

```bash
python main.py "Реализуй систему кеширования LRU" \
  --output-json report.json \
  --max-iterations 5
```

### Все параметры CLI

```
positional:
  requirement            Требование к проекту (строка)

optional:
  -f, --requirement-file FILE   Файл с требованием
  -i, --interactive             Интерактивный ввод
  -c, --config FILE             Конфиг (default: config.yaml)
  -o, --output-json FILE        Сохранить отчёт в JSON
  --max-iterations N            Переопределить лимит итераций
```

---

## Конфигурация (config.yaml)

```yaml
conductor:
  max_iterations_per_task: 3  # Максимум циклов CODER→TESTER→CRITIC на задачу
  verbose: true

agents:
  architect:
    kiro_role: kiro_planner
    timeout: 600
  coder:
    timeout: 480
  tester:
    timeout: 480
    test_run_timeout: 120     # Лимит запуска сгенерированных тестов
  critic:
    timeout: 360
```

---

## Артефакты сессии

Каждый запуск создаёт директорию `output/{session_id}/`:

| Файл | Содержимое |
|------|-----------|
| `architecture.json` | Архитектура + задачи от Architect |
| `coder_task_N.json` | Реализация задачи N (последняя итерация) |
| `tester_task_N.json` | Тесты + findings + результат запуска (`actual_run_output`) |
| `critic_task_N_iter_M.json` | Вердикт критика (итерация M) |
| `final_report.json` | Итоговый отчёт: статистика, force-approved задачи, список файлов |

Полный лог переходов сохраняется в `output/sessions/{session_id}.json`.

### Структура `final_report.json`

```json
{
  "session_id": "...",
  "original_requirement": "...",
  "total_tasks": 6,
  "completed_tasks": 6,
  "force_approved_tasks": 2,
  "force_approved_task_ids": [2, 4],
  "failed_tasks": 0,
  "total_iterations": 14,
  "output_dir": "output/...",
  "output_files": ["index.html", "js/app.js", "..."],
  "summary": "Completed 6/6 tasks (2 force-approved). Total iterations: 14.",
  "known_limitations": ["...описание проблем в force-approved задачах..."]
}
```

---

## Согласованность кода между агентами

Одна из ключевых проблем при генерации кода несколькими агентами — **рассогласованность имён свойств** (кодер объявляет `this._audio` в конструкторе, но использует `this.audio` в методах). Система решает это на трёх уровнях:

1. **Промт Coder** — явное правило: приватные поля всегда с `_` prefix, имена в конструкторе и методах должны совпадать буква в букву.
2. **Промт Critic** — обязательный скан каждого класса: несоответствие имени = `blocker`, автоматически → `NEEDS_REWORK`.
3. **Conductor parse-retry** — если кодер вернул невалидный JSON, pipeline не падает, а повторяет запрос с инструкцией по формату.

---

## Расширение системы

### Добавить нового агента

1. Создать `src/agents/myagent.py`, наследоваться от `BaseAgent`
2. Добавить prompt-шаблон `prompts/myagent.md` с нужными `{плейсхолдерами}`
3. Вызвать агента из `Conductor._process_task()`

### Изменить prompt агента

Все шаблоны — Markdown-файлы в `prompts/`. Переменные вида `{variable_name}` подставляются автоматически. Шаблоны читаются при каждом вызове — редактировать можно без перезапуска.

### Изменить поведение при force-approve

Логика в `Conductor._process_task()`: условие `state.iteration >= self.max_iterations` проверяется до вызова критика на последней итерации. Чтобы критик всё равно вызывался — переместите проверку после блока `CRITIC`.

---

## Требования

- Python 3.10+
- `kiro` CLI (`kiro_default`, `kiro_planner` roles)
- PyYAML ≥ 6.0

---

## Лицензия

MIT
