"""
src/conductor/conductor.py
Дирижёр — изолированный оркестратор всего pipeline разработки.

Flow:
  INIT → ARCHITECT → [для каждой задачи] CODER → TESTER → CRITIC
                                              ↑___NEEDS_REWORK___|
                                        APPROVED → следующая задача → DONE
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any

from src.agents.architect import ArchitectAgent
from src.agents.coder import CoderAgent
from src.agents.critic import CriticAgent
from src.agents.tester import TesterAgent
from src.utils.session_logger import SessionLogger


class Phase(str, Enum):
    INIT = "INIT"
    ARCHITECT = "ARCHITECT"
    CODER = "CODER"
    TESTER = "TESTER"
    CRITIC = "CRITIC"
    DONE = "DONE"
    ERROR = "ERROR"


@dataclass
class TaskState:
    """Состояние одной задачи в pipeline."""

    task: dict[str, Any]
    iteration: int = 0
    status: str = "pending"  # pending | running | approved | failed
    implemented_files: list[dict[str, Any]] = field(default_factory=list)
    test_result: dict[str, Any] = field(default_factory=dict)
    critic_result: dict[str, Any] = field(default_factory=dict)


class Conductor:
    """
    Изолированный дирижёр. Не пишет код, тесты или архитектуру.
    Только координирует агентов и управляет flow.
    """

    OUTPUT_BASE = Path(__file__).parent.parent.parent / "output"

    def __init__(
        self,
        max_iterations_per_task: int = 3,
        verbose: bool = True,
    ) -> None:
        self.max_iterations = max_iterations_per_task
        self.verbose = verbose

        # Сессия создаётся при запуске run()
        self._session_id: str = ""
        self._session_dir: Path = Path()
        self._logger: SessionLogger | None = None

        # Агенты инициализируются после создания session_dir
        self._architect: ArchitectAgent | None = None
        self._coder: CoderAgent | None = None
        self._tester: TesterAgent | None = None
        self._critic: CriticAgent | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, user_requirement: str) -> dict[str, Any]:
        """
        Запустить полный pipeline разработки.

        Args:
            user_requirement: исходное требование пользователя

        Returns:
            Финальный отчёт со всеми артефактами
        """
        self._init_session()
        self._print(f"🎼 Session started: {self._session_id}")
        self._print(f"📋 Requirement: {user_requirement[:200]}...")

        try:
            # Фаза 1: архитектор
            arch_result = self._run_architect(user_requirement)
            architecture = arch_result["architecture"]
            tasks = arch_result["tasks"]
            self._print(f"🏗️  Architecture ready. Tasks to implement: {len(tasks)}")

            # Определяем порядок выполнения задач
            architect = ArchitectAgent(self._logger)  # type: ignore[arg-type]
            task_waves = architect.get_task_order(tasks)

            # Состояния всех задач
            task_states: dict[int, TaskState] = {
                t["id"]: TaskState(task=t) for t in tasks
            }

            # Контекст файлов для кодера (накапливается по мере реализации)
            context_files: list[dict[str, Any]] = []

            # Фаза 2+: CODER → TESTER → CRITIC для каждой задачи
            for wave_idx, wave in enumerate(task_waves):
                self._print(f"\n🌊 Wave {wave_idx + 1}/{len(task_waves)}: {len(wave)} task(s)")
                for task in wave:
                    state = task_states[task["id"]]
                    approved = self._process_task(
                        state=state,
                        architecture=architecture,
                        user_requirement=user_requirement,
                        context_files=context_files,
                    )
                    # Добавляем реализованные файлы в контекст для следующих задач
                    if approved:
                        context_files.extend(state.implemented_files)

            # Финальный отчёт
            return self._build_final_report(
                user_requirement=user_requirement,
                task_states=task_states,
            )

        except Exception as exc:  # noqa: BLE001
            self._log_error(str(exc))
            return self._build_error_report(user_requirement, str(exc))

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    def _run_architect(self, user_requirement: str) -> dict[str, Any]:
        """Запустить архитектора и проверить вывод."""
        self._transition(Phase.ARCHITECT, "Running Architect agent")
        result = self._architect.design(  # type: ignore[union-attr]
            user_requirement=user_requirement,
            session_id=self._session_id,
        )
        self._log_state(
            phase=Phase.ARCHITECT,
            message=f"Architecture complete. {len(result['tasks'])} tasks generated.",
        )
        return result

    def _process_task(
        self,
        state: TaskState,
        architecture: dict[str, Any],
        user_requirement: str,
        context_files: list[dict[str, Any]],
    ) -> bool:
        """
        Обработать одну задачу через CODER → TESTER → CRITIC.
        Повторяет цикл при NEEDS_REWORK.

        Returns:
            True если задача одобрена, False если отвергнута после всех итераций
        """
        task = state.task
        rework_notes = ""

        self._print(f"\n  📌 Task {task['id']}: {task['title']}")

        while state.iteration < self.max_iterations:
            state.iteration += 1
            state.status = "running"
            self._print(f"    🔄 Iteration {state.iteration}/{self.max_iterations}")

            # --- CODER ---
            self._transition(Phase.CODER, f"Task {task['id']}, iter {state.iteration}")
            coder_result = self._coder.implement(  # type: ignore[union-attr]
                architecture=architecture,
                task=task,
                context_files=context_files,
                rework_notes=rework_notes,
            )
            state.implemented_files = coder_result["files"]
            self._print(f"      💻 Coder: {len(coder_result['files'])} file(s)")

            # --- TESTER ---
            self._transition(Phase.TESTER, f"Task {task['id']}, iter {state.iteration}")
            test_result = self._tester.test(  # type: ignore[union-attr]
                architecture=architecture,
                task=task,
                implemented_files=state.implemented_files,
            )
            state.test_result = test_result
            findings_count = len(test_result.get("findings", []))
            tests_passed = test_result.get("actual_run_output", {}).get("passed", "unknown")
            self._print(f"      🧪 Tester: {findings_count} finding(s), tests passed={tests_passed}")

            # --- CRITIC ---
            self._transition(Phase.CRITIC, f"Task {task['id']}, iter {state.iteration}")
            critic_result = self._critic.review(  # type: ignore[union-attr]
                user_requirement=user_requirement,
                task=task,
                implemented_files=state.implemented_files,
                tester_findings=test_result.get("findings", []),
                iteration=state.iteration,
                session_id=self._session_id,
            )
            state.critic_result = critic_result
            verdict = critic_result.get("verdict")
            score = critic_result.get("score")
            self._print(f"      🎯 Critic: {verdict} (score={score})")

            if self._critic.is_approved(critic_result):  # type: ignore[union-attr]
                state.status = "approved"
                self._print(f"    ✅ Task {task['id']} APPROVED")
                return True

            # NEEDS_REWORK
            rework_notes = self._critic.get_rework_instructions(critic_result)  # type: ignore[union-attr]
            self._print(f"    🔁 NEEDS_REWORK: {rework_notes[:200]}")

        # Всё равно NEEDS_REWORK после всех итераций
        state.status = "failed"
        self._print(f"    ❌ Task {task['id']} exhausted {self.max_iterations} iterations")
        return False

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _init_session(self) -> None:
        """Инициализировать новую сессию и все агенты."""
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self._session_dir = self.OUTPUT_BASE / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._logger = SessionLogger(self._session_id)

        self._architect = ArchitectAgent(self._logger)
        self._coder = CoderAgent(self._logger, output_dir=self._session_dir)
        self._tester = TesterAgent(self._logger, output_dir=self._session_dir)
        self._critic = CriticAgent(self._logger, max_iterations=self.max_iterations)

        self._logger.log(
            phase="INIT",
            agent="conductor",
            message="Session initialized",
            payload={"session_dir": str(self._session_dir)},
        )

    def _transition(self, phase: Phase, message: str) -> None:
        """Зафиксировать переход в новую фазу."""
        if self._logger:
            self._logger.log(
                phase=phase.value,
                agent="conductor",
                message=message,
            )

    def _log_state(self, phase: Phase, message: str, **extra: Any) -> None:
        if self._logger:
            self._logger.log(
                phase=phase.value,
                agent="conductor",
                message=message,
                payload=extra or None,
            )

    def _log_error(self, error: str) -> None:
        if self._logger:
            self._logger.log(
                phase="ERROR",
                agent="conductor",
                message=error,
            )

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def _build_final_report(
        self,
        user_requirement: str,
        task_states: dict[int, TaskState],
    ) -> dict[str, Any]:
        """Собрать итоговый отчёт."""
        approved = [s for s in task_states.values() if s.status == "approved"]
        failed = [s for s in task_states.values() if s.status == "failed"]

        all_files: list[str] = []
        for state in task_states.values():
            for f in state.implemented_files:
                all_files.append(f.get("path", ""))

        report: dict[str, Any] = {
            "session_id": self._session_id,
            "original_requirement": user_requirement,
            "total_tasks": len(task_states),
            "completed_tasks": len(approved),
            "failed_tasks": len(failed),
            "total_iterations": sum(s.iteration for s in task_states.values()),
            "output_dir": str(self._session_dir),
            "output_files": list(set(all_files)),
            "summary": (
                f"Completed {len(approved)}/{len(task_states)} tasks. "
                f"Total iterations: {sum(s.iteration for s in task_states.values())}."
            ),
            "known_limitations": [
                s.critic_result.get("summary", "")
                for s in failed
                if s.critic_result
            ],
        }

        if self._logger:
            self._logger.save_artifact("final_report.json", report)
            self._logger.log(
                phase="DONE",
                agent="conductor",
                message=f"Pipeline complete. {len(approved)}/{len(task_states)} tasks approved.",
            )

        self._transition(Phase.DONE, "Pipeline finished")
        self._print(f"\n🎉 Done! Report saved to {self._session_dir}/final_report.json")
        return report

    def _build_error_report(self, user_requirement: str, error: str) -> dict[str, Any]:
        """Отчёт при критической ошибке."""
        return {
            "session_id": self._session_id,
            "original_requirement": user_requirement,
            "status": "ERROR",
            "error": error,
            "output_dir": str(self._session_dir),
        }

    def _print(self, msg: str) -> None:
        if self.verbose:
            print(msg)  # noqa: T201
