#!/usr/bin/env python3
"""
main.py — точка входа AI Crew.

Использование:
  python main.py "Создай REST API для управления задачами (TODO-list)"
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


def build_conductor(config: dict):
    """Создать экземпляр Conductor с параметрами из конфига."""
    from src.conductor.conductor import Conductor

    conductor_cfg = config.get("conductor", {})
    return Conductor(
        max_iterations_per_task=conductor_cfg.get("max_iterations_per_task", 3),
        verbose=conductor_cfg.get("verbose", True),
    )


def run_pipeline(requirement: str, config: dict) -> dict:
    """Запустить полный pipeline для заданного требования."""
    conductor = build_conductor(config)
    return conductor.run(requirement)


def interactive_mode(config: dict) -> None:
    """Интерактивный режим ввода требования."""
    print("=" * 60)
    print("🎼  AI Crew — Interactive Mode")
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

    result = run_pipeline(requirement, config)
    _print_summary(result)


def _print_summary(result: dict) -> None:
    """Вывести итоговый отчёт в читаемом виде."""
    print("\n" + "=" * 60)
    print("📊  ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 60)
    if result.get("status") == "ERROR":
        print(f"❌ Ошибка: {result.get('error')}")
        return

    print(f"✅ Сессия:      {result.get('session_id')}")
    print(f"📁 Артефакты:  {result.get('output_dir')}")
    print(f"📌 Задач:       {result.get('completed_tasks')}/{result.get('total_tasks')}")
    print(f"🔄 Итераций:   {result.get('total_iterations')}")
    print(f"📝 Итог:        {result.get('summary')}")

    if result.get("output_files"):
        print(f"\n📂 Созданные файлы ({len(result['output_files'])}):")
        for f in sorted(result["output_files"]):
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
  python main.py "Создай REST API для управления задачами"
  python main.py --requirement-file requirements.txt
  python main.py --interactive
  python main.py "Сделай парсер CSV" --config config.yaml --output-json result.json
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

    # Определяем требование
    if args.interactive:
        interactive_mode(config)
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
    result = run_pipeline(requirement, config)

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
