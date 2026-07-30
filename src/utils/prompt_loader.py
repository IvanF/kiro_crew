"""
src/utils/prompt_loader.py
Загружает prompt-шаблоны из директории prompts/ и подставляет переменные.
"""
from __future__ import annotations

import re
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def load_prompt(template_name: str, **kwargs: object) -> str:
    """
    Загружает шаблон из файла <template_name>.md и подставляет переменные.

    Args:
        template_name: имя файла без расширения (architect, coder, tester, critic, conductor)
        **kwargs: пары ключ=значение для подстановки {key} в шаблоне

    Returns:
        Готовый prompt-строкой

    Raises:
        FileNotFoundError: если шаблон не найден
    """
    path = PROMPTS_DIR / f"{template_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    template = path.read_text(encoding="utf-8")

    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        template = template.replace(placeholder, str(value))

    # Проверяем незаполненные плейсхолдеры
    remaining = re.findall(r"\{(\w+)\}", template)
    if remaining:
        # Оставшиеся плейсхолдеры заменяем пустыми строками (не обязательные поля)
        for key in set(remaining):
            template = template.replace("{" + key + "}", "")

    return template
