"""
src/agents/base.py
Базовый класс для всех агентов. Содержит общую логику:
- загрузка промт-шаблона
- вызов kiro через kiro_runner
- парсинг JSON-ответа
- логирование
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from src.utils.kiro_runner import parse_json_response, run_kiro_agent
from src.utils.prompt_loader import load_prompt
from src.utils.session_logger import SessionLogger


class BaseAgent(ABC):
    """Абстрактный базовый агент."""

    #: Имя файла шаблона без расширения (совпадает с именем агента)
    template_name: str = ""

    #: Роль kiro агента
    kiro_role: str = "kiro_default"

    #: Максимальное время ожидания ответа (секунды)
    timeout: int = 300

    def __init__(self, session_logger: SessionLogger) -> None:
        self.logger = session_logger

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """
        Запустить агент с переданными параметрами.

        Args:
            **kwargs: переменные для подстановки в prompt-шаблон

        Returns:
            Распарсенный JSON-ответ агента

        Raises:
            RuntimeError: при ошибке kiro или парсинга
        """
        prompt = self._build_prompt(**kwargs)
        self.logger.log(
            phase=self.template_name.upper(),
            agent=self.template_name,
            message="Agent started",
            payload={"prompt_length": len(prompt)},
        )

        t_start = time.monotonic()
        raw_response = run_kiro_agent(prompt, role=self.kiro_role, timeout=self.timeout)
        elapsed = round(time.monotonic() - t_start, 2)

        self.logger.log(
            phase=self.template_name.upper(),
            agent=self.template_name,
            message=f"Agent responded in {elapsed}s",
            payload={"response_length": len(raw_response)},
        )

        result = parse_json_response(raw_response)
        self._validate(result)

        self.logger.log(
            phase=self.template_name.upper(),
            agent=self.template_name,
            message="Output validated successfully",
        )
        return result

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def _validate(self, result: dict[str, Any]) -> None:
        """
        Валидирует структуру JSON-ответа агента.

        Raises:
            ValueError: если структура неверна
        """

    def _build_prompt(self, **kwargs: Any) -> str:
        """Загружает и заполняет шаблон промта."""
        return load_prompt(self.template_name, **kwargs)
