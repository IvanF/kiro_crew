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
    # --wrap never — отключает перенос строк, чтобы не ломать JSON-вывод
    cmd = [kiro_bin, "chat", "--no-interactive", "--agent", role, "--trust-all-tools", "--wrap", "never"]

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


def parse_json_response(raw: str, required_keys: list[str] | None = None) -> dict[str, Any]:
    """
    Извлекает JSON из ответа агента.

    Агент может обернуть JSON в markdown-блок ```json ... ``` или в
    терминально-рендеренный blockquote (> json ...). Перед JSON могут
    идти логи работы инструментов (tool-output). Функция ищет все
    JSON-кандидаты и возвращает последний валидный объект.

    Args:
        raw: сырой текстовый ответ агента
        required_keys: если задан — объект должен содержать хотя бы один из этих ключей

    Returns:
        Распарсенный словарь

    Raises:
        ValueError: если JSON не найден или невалиден
    """
    import re

    # Попытка 1: весь ответ — чистый JSON
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Попытка 2: извлечь из ```json ... ``` блока
    # Ищем все такие блоки, берём последний (агент пишет финальный JSON после рассуждений)
    for match in reversed(list(re.finditer(r"```(?:json)?\s*(\{.+?})\s*```", raw, re.DOTALL))):
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict):
                if not required_keys or any(k in obj for k in required_keys):
                    return obj
        except json.JSONDecodeError:
            continue

    # Попытка 3: kiro рендерит блоки как "> json\n> {...}" — снимаем "> " prefix
    cleaned = re.sub(r"^>\s?", "", raw, flags=re.MULTILINE)
    if cleaned != raw:
        candidates = _extract_all_balanced_json(cleaned)
        for candidate in reversed(candidates):
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    if not required_keys or any(k in obj for k in required_keys):
                        return obj
            except json.JSONDecodeError:
                continue

    # Попытка 4: найти все { ... } блоки в исходном тексте, взять последний валидный
    candidates = _extract_all_balanced_json(raw)
    for candidate in reversed(candidates):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                if not required_keys or any(k in obj for k in required_keys):
                    return obj
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse JSON from agent response. Raw (first 500 chars): {raw[:500]}")


def _extract_balanced_json(text: str) -> str | None:
    """
    Находит первый сбалансированный JSON-объект в тексте.
    Оставлен для обратной совместимости.
    """
    results = _extract_all_balanced_json(text)
    return results[0] if results else None


def _extract_all_balanced_json(text: str) -> list[str]:
    """
    Находит ВСЕ сбалансированные JSON-объекты верхнего уровня в тексте,
    корректно обрабатывая вложенные скобки и строки с экранированием.

    Returns:
        Список строк с JSON-объектами (в порядке появления в тексте)
    """
    results = []
    pos = 0
    while pos < len(text):
        start = text.find("{", pos)
        if start == -1:
            break

        depth = 0
        in_string = False
        escape_next = False
        end = None

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
                    end = i
                    break

        if end is not None:
            results.append(text[start : end + 1])
            pos = end + 1
        else:
            break  # незакрытый блок — дальше нет смысла искать

    return results
