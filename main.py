#!/usr/bin/env python3
"""
main.py — точка входа AI Crew.

Режим создания (по умолчанию):
  python main.py "Создай REST API для управления задачами"

Режим редактирования существующего проекта:
  python main.py "Добавь пагинацию в /users endpoint" --project-dir ./my_api

Прочие варианты:
  python main.py --requirement-file req.txt
  python main.py --interactive
  python main.py --help
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """Загрузить конфигурацию из YAML-файла."""
    path = Path(config_path)
    if not path.exists():
        print(f"[WARN] Config file '{config_path}' not found, using defaults")
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_conductor(config: dict, project_dir: Path | None = None):
    """Создать экземпляр Conductor с параметрами из конфига."""
    from src.conductor.conductor import Conductor

    conductor_cfg = config.get("conductor", {})
    return Conductor(
        max_iterations_per_task=conductor_cfg.get("max_iterations_per_task", 3),
        verbose=conductor_cfg.get("verbose", True),
        project_dir=project_dir,
    )


def run_pipeline(requirement: str, config: dict, project_dir: Path | None = None) -> dict:
    """Запустить полный pipeline для заданного требования."""
    conductor = build_conductor(config, project_dir=project_dir)
    return conductor.run(requirement)


def interactive_mode(config: dict, project_dir: Path | None = None) -> None:
    """Интерактивный режим ввода требования."""
    print("=" * 60)
    print("🎼  AI Crew — Interactive Mode")
    if project_dir:
        print(f"✏️   Edit mode: {project_dir}")
    print("=" * 60)
    print("Введите требование к проекту (завершите ввод пустой строкой):")
    print()

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "" and lines:
            break
        lines.append(line)

    requirement = "\n".join(lines).strip()
    if not requirement:
        print("Требование не введено. Выход.")
        sys.exit(1)

    print(f"\n📋 Требование ({len(requirement)} символов):\n{requirement}\n")
    confirm = input("Запустить pipeline? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes", "д", "да"):
        print("Отмена.")
        sys.exit(0)

    result = run_pipeline(requirement, config, project_dir=project_dir)
    _print_summary(result)


def _print_summary(result: dict) -> None:
    """Вывести итоговый отчёт в читаемом виде."""
    print("\n" + "=" * 60)
    print("📊  ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 60)
    if result.get("status") == "ERROR":
        print(f"❌ Ошибка: {result.get('error')}")
        return

    mode = "✏️  edit" if result.get("project_dir") else "🆕 create"
    print(f"🎯 Режим:       {mode}")
    print(f"✅ Сессия:      {result.get('session_id')}")
    if result.get("project_dir"):
        print(f"📂 Проект:      {result.get('project_dir')}")
    print(f"📁 Артефакты:  {result.get('output_dir')}")
    print(f"📌 Задач:       {result.get('completed_tasks')}/{result.get('total_tasks')}")
    print(f"🔄 Итераций:   {result.get('total_iterations')}")
    print(f"📝 Итог:        {result.get('summary')}")

    modified = result.get("modified_files", [])
    created = result.get("created_files", [])
    deleted = result.get("deleted_files", [])

    if modified:
        print(f"\n✏️   Изменённые файлы ({len(modified)}):")
        for f in sorted(modified):
            print(f"   • {f}")
    if created:
        print(f"\n➕  Созданные файлы ({len(created)}):")
        for f in sorted(created):
            print(f"   • {f}")
    if deleted:
        print(f"\n🗑️   Удалённые файлы ({len(deleted)}):")
        for f in sorted(deleted):
            print(f"   • {f}")

    # Обратная совместимость — режим create выводит output_files
    output_files = result.get("output_files", [])
    if output_files and not modified and not created:
        print(f"\n📂 Созданные файлы ({len(output_files)}):")
        for f in sorted(output_files):
            print(f"   • {f}")

    limitations = result.get("known_limitations", [])
    if limitations:
        print("\n⚠️  Известные ограничения:")
        for lim in limitations:
            if lim:
                print(f"   • {lim}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Crew — команда нейросетевых агентов для разработки ПО",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  # Создать новый проект
  python main.py "Создай REST API для управления задачами"

  # Редактировать существующий проект
  python main.py "Добавь пагинацию в endpoint /users" --project-dir ./my_api
  python main.py "Исправь все предупреждения линтера" --project-dir ./my_app

  # Из файла с требованием
  python main.py --requirement-file requirements.txt --project-dir ./my_api

  # Интерактивный режим
  python main.py --interactive --project-dir ./my_project

  # С сохранением отчёта
  python main.py "Добавь тесты" --project-dir ./app --output-json report.json
        """,
    )
    parser.add_argument(
        "requirement",
        nargs="?",
        help="Требование к проекту (строка)",
    )
    parser.add_argument(
        "--requirement-file", "-f",
        metavar="FILE",
        help="Файл с требованием (текстовый)",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Интерактивный ввод требования",
    )
    parser.add_argument(
        "--project-dir", "-p",
        metavar="DIR",
        help=(
            "Путь к существующему проекту для редактирования. "
            "Если указан — активируется режим edit: агенты получают контекст "
            "существующего кода и вносят точечные правки."
        ),
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        metavar="FILE",
        help="Путь к конфигурационному файлу (default: config.yaml)",
    )
    parser.add_argument(
        "--output-json", "-o",
        metavar="FILE",
        help="Сохранить итоговый отчёт в JSON-файл",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        help="Максимум итераций доработки (переопределяет config.yaml)",
    )

    args = parser.parse_args()

    # Загружаем конфиг
    config = load_config(args.config)

    # Переопределяем параметры из CLI
    if args.max_iterations is not None:
        config.setdefault("conductor", {})["max_iterations_per_task"] = args.max_iterations

    # Валидируем --project-dir
    project_dir: Path | None = None
    if args.project_dir:
        project_dir = Path(args.project_dir).resolve()
        if not project_dir.exists():
            print(f"❌ --project-dir не найден: {project_dir}")
            sys.exit(1)
        if not project_dir.is_dir():
            print(f"❌ --project-dir не является директорией: {project_dir}")
            sys.exit(1)

    # Определяем требование
    if args.interactive:
        interactive_mode(config, project_dir=project_dir)
        return

    requirement = ""
    if args.requirement:
        requirement = args.requirement.strip()
    elif args.requirement_file:
        path = Path(args.requirement_file)
        if not path.exists():
            print(f"❌ Файл не найден: {args.requirement_file}")
            sys.exit(1)
        requirement = path.read_text(encoding="utf-8").strip()

    if not requirement:
        parser.print_help()
        print("\n❌ Укажите требование: аргументом, --requirement-file или --interactive")
        sys.exit(1)

    # Запускаем pipeline
    result = run_pipeline(requirement, config, project_dir=project_dir)

    # Сохраняем JSON-отчёт если запрошено
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"📄 Отчёт сохранён: {out_path}")

    _print_summary(result)

    # Код выхода: 0 если всё ок, 1 если были ошибки
    sys.exit(0 if result.get("status") != "ERROR" else 1)


if __name__ == "__main__":
    main()
