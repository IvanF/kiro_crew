"""
src/utils/kiro_runner.py
Обёртка над kiro_cli subagent API.
Запускает агент через subagent pipeline и возвращает ответ как строку.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def run_kiro_agent(
    prompt: str,
    role: str = "kiro_default",
    timeout: int = 300,
) -> str:
    """
    Запускает одиночный kiro агент с заданным промтом.

    Использует kiro CLI в non-interactive режиме: передаёт промт через stdin
    и возвращает stdout как строку.

    Args:
        prompt: системный + пользовательский промт для агента
        role: роль агента (kiro_default, kiro_planner, kiro_guide)
        timeout: максимальное время ожидания в секундах

    Returns:
        Ответ агента как строка

    Raises:
        RuntimeError: при ошибке запуска или таймауте
    """
    # Ищем бинарник kiro в нескольких вариантах имён
    import shutil

    kiro_bin = (
        shutil.which("kiro-cli-chat")
        or shutil.which("kiro-cli")
        or shutil.which("kiro")
    )
    if kiro_bin is None:
        raise RuntimeError(
            "kiro CLI not found. Make sure 'kiro-cli-chat' (or 'kiro') is installed and in PATH. "
            "Checked: kiro-cli-chat, kiro-cli, kiro."
        )

    # --agent задаёт профиль агента (kiro_default / kiro_planner / kiro_guide)
    cmd = [kiro_bin, "chat", "--no-interactive", "--agent", role, "--trust-all-tools"]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 and result.stderr:
            raise RuntimeError(
                f"kiro agent exited with code {result.returncode}: {result.stderr[:500]}"
            )
        output = result.stdout.strip()
        # Убираем ANSI escape-коды (цвет, перемещение курсора и т.д.)
        output = _strip_ansi(output)
        return output
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"kiro agent timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"kiro CLI binary not found at '{kiro_bin}'. Make sure it is installed and in PATH."
        ) from exc


def _strip_ansi(text: str) -> str:
    """Удалить ANSI/VT100 escape-последовательности из текста."""
    import re
    # CSI sequences: ESC [ ... final-byte  (цвет, курсор, стирание и т.д.)
    # OSC sequences: ESC ] ... ST/BEL      (заголовок окна, гиперссылки)
    # Single-char sequences: ESC + one char
    ansi_escape = re.compile(
        r"\x1B"
        r"(?:"
        r"\[[0-?]*[ -/]*[@-~]"   # CSI sequence
        r"|\][^\x07\x1B]*(?:\x07|\x1B\\)"  # OSC sequence (BEL или ST)
        r"|[@-Z\\-_]"            # Fe sequence (одиночный символ)
        r")"
    )
    return ansi_escape.sub("", text)


def parse_json_response(raw: str) -> dict[str, Any]:
    """
    Извлекает JSON из ответа агента.

    Агент может обернуть JSON в markdown-блок ```json ... ``` или в
    терминально-рендеренный blockquote (> json ...). Функция ищет
    первый валидный JSON-объект используя балансировку скобок.

    Args:
        raw: сырой текстовый ответ агента

    Returns:
        Распарсенный словарь

    Raises:
        ValueError: если JSON не найден или невалиден
    """
    import re

    # Попытка 1: весь ответ — чистый JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Попытка 2: извлечь из ```json ... ``` блока (жадный захват до закрывающих ```)
    match = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Попытка 3: kiro CLI рендерит markdown-блоки как "> json\n> {...}"
    # Убираем строчный префикс "> " и пробуем снова
    cleaned = re.sub(r"^>\s?", "", raw, flags=re.MULTILINE)
    if cleaned != raw:
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Ищем { ... } в очищенном тексте
        candidate = _extract_balanced_json(cleaned)
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # Попытка 4: балансировка скобок — находим самый длинный корректный JSON-объект
    candidate = _extract_balanced_json(raw)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from agent response. Raw (first 500 chars): {raw[:500]}")


def _extract_balanced_json(text: str) -> str | None:
    """
    Находит первый сбалансированный JSON-объект в тексте,
    корректно обрабатывая вложенные скобки и строки с экранированием.

    Returns:
        Строку с JSON-объектом или None если не найдено
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None
