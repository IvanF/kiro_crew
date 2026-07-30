"""
src/agents/critic.py
Агент-критик: сравнивает результат с требованиями и выносит вердикт
APPROVED или NEEDS_REWORK.

Примечание: решение о force-approve при исчерпании итераций принимается
кондуктором (_process_task), а не здесь — это устраняет race condition
и делает логику ветвления централизованной.
"""
from __future__ import annotations

import json
from typing import Any

from src.agents.base import BaseAgent
from src.utils.session_logger import SessionLogger


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
    ) -> None:
        super().__init__(session_logger)

    def review(
        self,
        user_requirement: str,
        task: dict[str, Any],
        implemented_files: list[dict[str, Any]],
        tester_findings: list[dict[str, Any]],
        test_run_output: dict[str, Any] | None = None,
        iteration: int = 1,
        session_id: str = "",
    ) -> dict[str, Any]:
        """
        Провести код-ревью для одной задачи.

        Args:
            user_requirement: исходное требование пользователя
            task: спецификация задачи от архитектора
            implemented_files: файлы от кодера
            tester_findings: список находок от тестировщика
            test_run_output: фактический результат запуска тестов (exit_code, stdout, passed)
            iteration: номер текущей итерации доработки
            session_id: ID сессии

        Returns:
            Вердикт с полным отчётом
        """
        result = self.run(
            user_requirement=user_requirement,
            task=json.dumps(task, ensure_ascii=False, indent=2),
            implemented_files=json.dumps(implemented_files, ensure_ascii=False, indent=2),
            tester_findings=json.dumps(tester_findings, ensure_ascii=False, indent=2),
            test_run_output=json.dumps(test_run_output or {}, ensure_ascii=False, indent=2),
            iteration=str(iteration),
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
