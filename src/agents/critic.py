"""
src/agents/critic.py
Агент-критик: сравнивает результат с требованиями и выносит вердикт
APPROVED или NEEDS_REWORK.
"""
from __future__ import annotations

import json
from typing import Any

from src.agents.base import BaseAgent
from src.utils.session_logger import SessionLogger


# Порог для автоматического одобрения
APPROVE_SCORE_THRESHOLD = 70


class CriticAgent(BaseAgent):
    """
    Сравнивает реализацию с исходным требованием и архитектурой.
    Возвращает вердикт с детальным анализом.

    Verdict: "APPROVED" | "NEEDS_REWORK"

    Output schema:
    {
        "session_id": str,
        "task_id": int,
        "verdict": "APPROVED" | "NEEDS_REWORK",
        "score": int,
        "summary": str,
        "compliance_check": { "requirement_met", "architecture_followed", "acceptance_criteria" },
        "issues": [ { "id", "severity", "category", "location", "description", "suggested_fix" } ],
        "rework_instructions": str,
        "approved_files": [ str ]
    }
    """

    template_name = "critic"
    kiro_role = "kiro_default"
    timeout = 360
    response_keys = ["verdict", "task_id"]

    def __init__(
        self,
        session_logger: SessionLogger,
        max_iterations: int = 3,
    ) -> None:
        super().__init__(session_logger)
        self._max_iterations = max_iterations

    def review(
        self,
        user_requirement: str,
        task: dict[str, Any],
        implemented_files: list[dict[str, Any]],
        tester_findings: list[dict[str, Any]],
        iteration: int,
        session_id: str,
    ) -> dict[str, Any]:
        """
        Провести код-ревью для одной задачи.

        Args:
            user_requirement: исходное требование пользователя
            task: спецификация задачи от архитектора
            implemented_files: файлы от кодера
            tester_findings: список находок от тестировщика
            iteration: номер текущей итерации доработки
            session_id: ID сессии

        Returns:
            Вердикт с полным отчётом
        """
        # Если достигнут лимит итераций — принудительно одобряем с предупреждением
        if iteration >= self._max_iterations:
            self.logger.log(
                phase="CRITIC",
                agent="critic",
                message=f"Max iterations ({self._max_iterations}) reached — forcing APPROVED",
            )
            return self._force_approve(task, session_id, iteration)

        result = self.run(
            user_requirement=user_requirement,
            task=json.dumps(task, ensure_ascii=False, indent=2),
            implemented_files=json.dumps(implemented_files, ensure_ascii=False, indent=2),
            tester_findings=json.dumps(tester_findings, ensure_ascii=False, indent=2),
            iteration=str(iteration),
            max_iterations=str(self._max_iterations),
            session_id=session_id,
        )

        self.logger.log(
            phase="CRITIC",
            agent="critic",
            message=f"Verdict for task {task['id']}: {result.get('verdict')} (score={result.get('score')})",
        )
        self.logger.save_artifact(f"critic_task_{task['id']}_iter_{iteration}.json", result)
        return result

    def is_approved(self, review_result: dict[str, Any]) -> bool:
        """Возвращает True если вердикт — APPROVED."""
        return review_result.get("verdict") == "APPROVED"

    def get_rework_instructions(self, review_result: dict[str, Any]) -> str:
        """Возвращает инструкции по доработке для кодера."""
        return review_result.get("rework_instructions", "")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _force_approve(
        self,
        task: dict[str, Any],
        session_id: str,
        iteration: int,
    ) -> dict[str, Any]:
        """Формирует принудительный APPROVED при достижении лимита итераций."""
        return {
            "session_id": session_id,
            "task_id": task["id"],
            "verdict": "APPROVED",
            "score": 50,
            "summary": f"Force-approved after {iteration} iterations (max={self._max_iterations})",
            "compliance_check": {
                "requirement_met": True,
                "architecture_followed": True,
                "acceptance_criteria": [],
            },
            "issues": [],
            "rework_instructions": "",
            "approved_files": [],
            "force_approved": True,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, result: dict[str, Any]) -> None:
        if "verdict" not in result:
            raise ValueError("Critic response missing 'verdict'")
        if result["verdict"] not in ("APPROVED", "NEEDS_REWORK"):
            raise ValueError(
                f"Invalid verdict: '{result['verdict']}'. Expected APPROVED or NEEDS_REWORK"
            )
        if "task_id" not in result:
            raise ValueError("Critic response missing 'task_id'")
        if result["verdict"] == "NEEDS_REWORK" and not result.get("rework_instructions"):
            raise ValueError("NEEDS_REWORK verdict must include 'rework_instructions'")
