"""
src/agents/architect.py
Агент-архитектор: анализирует требования, проектирует архитектуру
и разбивает проект на атомарные задачи для Кодера.
"""
from __future__ import annotations

import json
from typing import Any

from src.agents.base import BaseAgent
from src.utils.session_logger import SessionLogger


class ArchitectAgent(BaseAgent):
    """
    Принимает текстовый запрос пользователя и возвращает:
    - полную архитектуру системы (модули, стек, потоки данных)
    - нумерованный список задач для Кодера

    Output schema:
    {
        "architecture": { "overview", "tech_stack", "modules", "data_flow", "assumptions" },
        "tasks": [ { "id", "module", "title", "description", "inputs", "outputs",
                      "acceptance_criteria", "dependencies" } ]
    }
    """

    template_name = "architect"
    kiro_role = "kiro_planner"
    timeout = 600  # архитектор может думать дольше
    response_keys = ["architecture", "tasks"]

    def __init__(self, session_logger: SessionLogger) -> None:
        super().__init__(session_logger)

    def design(
        self,
        user_requirement: str,
        session_id: str,
        project_snapshot: dict[str, Any] | None = None,
        edit_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Запустить архитектора для заданного требования.

        Args:
            user_requirement: исходный текст требования от пользователя
            session_id: идентификатор текущей сессии
            project_snapshot: snapshot существующего проекта (edit-режим)
            edit_mode: True — используется промт architect_edit.md

        Returns:
            Словарь с ключами 'architecture' и 'tasks'
        """
        # В edit-режиме используем специальный промт
        original_template = self.template_name
        if edit_mode:
            self.template_name = "architect_edit"

        try:
            result = self.run(
                user_requirement=user_requirement,
                session_id=session_id,
                project_snapshot=json.dumps(
                    project_snapshot or {}, ensure_ascii=False, indent=2
                ),
            )
        finally:
            self.template_name = original_template

        # Сохраняем архитектуру как артефакт
        self.logger.save_artifact("architecture.json", result)
        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, result: dict[str, Any]) -> None:
        """Проверяет, что ответ содержит обязательные поля."""
        if "architecture" not in result:
            raise ValueError("Architect response missing 'architecture' key")
        if "tasks" not in result:
            raise ValueError("Architect response missing 'tasks' key")
        if not isinstance(result["tasks"], list):
            raise ValueError("'tasks' must be a list")
        if len(result["tasks"]) == 0:
            raise ValueError("Architect must produce at least one task")

        arch = result["architecture"]
        for required in ("overview", "tech_stack", "modules"):
            if required not in arch:
                raise ValueError(f"Architecture missing required field: '{required}'")

        for i, task in enumerate(result["tasks"]):
            for field in ("id", "title", "description", "acceptance_criteria"):
                if field not in task:
                    raise ValueError(
                        f"Task #{i} missing required field: '{field}'"
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_task_order(self, tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """
        Возвращает задачи, сгруппированные по волнам (topological sort по зависимостям).
        Задачи без зависимостей — первая волна, потом следующие.

        Args:
            tasks: список задач от архитектора

        Returns:
            Список волн, каждая волна — список задач, которые можно запустить параллельно
        """
        id_map = {task["id"]: task for task in tasks}
        completed: set[int] = set()
        waves: list[list[dict[str, Any]]] = []

        remaining = list(tasks)
        while remaining:
            wave = [
                t for t in remaining
                if all(dep in completed for dep in t.get("dependencies", []))
            ]
            if not wave:
                # Циклические зависимости — добавляем всё оставшееся в одну волну
                wave = remaining
            for t in wave:
                completed.add(t["id"])
            waves.append(wave)
            remaining = [t for t in remaining if t["id"] not in completed]

        return waves
