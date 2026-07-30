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
    """Удалить ANSI escape-последовательности из текста."""
    import re
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def parse_json_response(raw: str) -> dict[str, Any]:
    """
    Извлекает JSON из ответа агента.

    Агент может обернуть JSON в markdown-блок ```json ... ```.
    Функция ищет первый валидный JSON-объект в тексте.

    Args:
        raw: сырой текстовый ответ агента

    Returns:
        Распарсенный словарь

    Raises:
        ValueError: если JSON не найден или невалиден
    """
    # Попытка 1: весь ответ — чистый JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Попытка 2: извлечь из ```json ... ``` блока
    import re

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Попытка 3: найти первый { ... } блок
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from agent response. Raw (first 500 chars): {raw[:500]}")
