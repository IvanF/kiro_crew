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
└──────────────┬──────────────────────────────────────────┘
               │
   ┌───────────▼───────────┐
   │     ARCHITECT         │  kiro_planner
   │  Анализирует промт.   │  → Выдаёт архитектуру + список задач для кодера
   └───────────┬───────────┘
               │ tasks[]
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
| **Conductor** | — (Python) | State machine, маршрутизация, лимиты итераций |

---

## Flow

```
INIT
  └─▶ ARCHITECT
        └─▶ [Волна 1: задачи без зависимостей]
              └─▶ CODER (итерация N)
                    └─▶ TESTER
                          └─▶ CRITIC
                                ├─ APPROVED ──▶ следующая задача
                                └─ NEEDS_REWORK ──▶ CODER (итерация N+1)
                                                     (max_iterations раз)
        └─▶ [Волна 2 ...] ...
  └─▶ DONE — финальный отчёт
```

**Ключевые свойства:**
- Задачи с зависимостями выполняются после зависимых (topological sort → волны)
- Каждый агент получает контекст всех ранее реализованных файлов
- При `NEEDS_REWORK` Критик передаёт конкретные инструкции кодеру
- Принудительный `APPROVED` при достижении лимита итераций (защита от бесконечного цикла)

---

## Структура проекта

```
ai_crew/
├── main.py                    # Точка входа, CLI
├── config.yaml                # Конфигурация (таймауты, итерации)
├── requirements.txt
│
├── prompts/                   # Prompt-шаблоны агентов
│   ├── architect.md
│   ├── coder.md
│   ├── tester.md
│   ├── critic.md
│   └── conductor.md
│
├── src/
│   ├── agents/
│   │   ├── base.py            # Абстрактный BaseAgent
│   │   ├── architect.py       # ArchitectAgent
│   │   ├── coder.py           # CoderAgent
│   │   ├── tester.py          # TesterAgent
│   │   └── critic.py          # CriticAgent
│   ├── conductor/
│   │   └── conductor.py       # Conductor (дирижёр)
│   └── utils/
│       ├── kiro_runner.py     # Обёртка kiro CLI
│       ├── prompt_loader.py   # Загрузка шаблонов
│       └── session_logger.py  # Лог сессии (JSON)
│
└── output/
    └── sessions/
        └── {session_id}/      # Артефакты каждой сессии
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

# 3. Проверьте структуру
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
  -f FILE                Файл с требованием
  -i                     Интерактивный ввод
  -c FILE                Конфиг (default: config.yaml)
  -o FILE                Сохранить отчёт в JSON
  --max-iterations N     Переопределить лимит итераций
```

---

## Конфигурация (config.yaml)

```yaml
conductor:
  max_iterations_per_task: 3  # Максимум циклов доработки на задачу
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
    approve_score_threshold: 70
```

---

## Артефакты сессии

Каждый запуск создаёт директорию `output/{session_id}/`:

| Файл | Содержимое |
|------|-----------|
| `architecture.json` | Архитектура + задачи от Architect |
| `coder_task_N.json` | Реализация задачи N |
| `tester_task_N.json` | Тесты + отчёт + результат запуска |
| `critic_task_N_iter_M.json` | Вердикт критика (итерация M) |
| `final_report.json` | Итоговый отчёт всей сессии |

Лог сессии сохраняется в `output/sessions/{session_id}.json`.

---

## Расширение системы

### Добавить нового агента

1. Создать `src/agents/myagent.py`, наследоваться от `BaseAgent`
2. Добавить prompt-шаблон `prompts/myagent.md`
3. Вызвать агента из `Conductor._process_task()`

### Изменить prompt агента

Все шаблоны — Markdown-файлы в `prompts/`. Переменные вида `{variable_name}` автоматически подставляются. Редактируйте без перезапуска — шаблоны читаются при каждом вызове.

---

## Требования

- Python 3.10+
- `kiro` CLI (`kiro_default`, `kiro_planner` roles)
- PyYAML 6.0.2

---

## Лицензия

MIT
