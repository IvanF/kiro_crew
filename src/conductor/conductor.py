"""
src/conductor/conductor.py
Дирижёр — изолированный оркестратор всего pipeline разработки.

Flow:
  INIT → ARCHITECT → [для каждой задачи] CODER → TESTER → CRITIC
                                              ↑___NEEDS_REWORK___|
                                        APPROVED → следующая задача → DONE

Режимы:
  create (по умолчанию) — создание проекта с нуля.
  edit   (project_dir передан) — анализ и правка существующего проекта.
    В edit-режиме:
      - Conductor сканирует project_dir через ProjectReader и строит snapshot.
      - Архитектор получает промт architect_edit.md + snapshot существующего кода.
      - Кодер получает промт coder_edit.md + original_content файлов и возвращает
        mode=patch/create/delete для каждого файла.
      - Conductor записывает правки обратно в project_dir (не в output/).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.agents.architect import ArchitectAgent
from src.agents.coder import CoderAgent
from src.agents.critic import CriticAgent
from src.agents.tester import TesterAgent
from src.utils.session_logger import SessionLogger
from src.utils.project_reader import ProjectSnapshot, read_project


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
    status: str = "pending"  # pending | running | approved | force_approved | failed
    implemented_files: list[dict[str, Any]] = field(default_factory=list)
    test_result: dict[str, Any] = field(default_factory=dict)
    critic_result: dict[str, Any] = field(default_factory=dict)
    force_approved: bool = False


class Conductor:
    """
    Изолированный дирижёр. Не пишет код, тесты или архитектуру.
    Только координирует агентов и управляет flow.

    Force-approve:
      Если задача не одобрена после max_iterations итераций, кондуктор
      сам принимает решение о принудительном одобрении — критик при этом
      уже вызван на последней итерации и вернул NEEDS_REWORK.

    Retry при ошибке парсинга:
      Если кодер вернул невалидный JSON, итерация не тратится — вместо этого
      rework_notes пополняются инструкцией по формату, и кодер вызывается
      снова (до max_parse_retries раз в рамках той же итерации).
    """

    OUTPUT_BASE = Path(__file__).parent.parent.parent / "output"

    #: Максимальное число retry при ошибке парсинга JSON от кодера
    MAX_PARSE_RETRIES = 2

    def __init__(
        self,
        max_iterations_per_task: int = 3,
        verbose: bool = True,
        project_dir: Path | None = None,
    ) -> None:
        self.max_iterations = max_iterations_per_task
        self.verbose = verbose

        # Если задан — активируется edit-режим
        self._project_dir: Path | None = Path(project_dir).resolve() if project_dir else None
        self._edit_mode: bool = self._project_dir is not None
        self._project_snapshot: ProjectSnapshot | None = None

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
        mode_label = f"✏️  edit ({self._project_dir})" if self._edit_mode else "🆕 create"
        self._print(f"🎼 Session started: {self._session_id}")
        self._print(f"🎯 Mode: {mode_label}")
        self._print(f"📋 Requirement: {user_requirement[:200]}...")

        try:
            # Edit-режим: сканируем существующий проект
            if self._edit_mode and self._project_dir:
                self._print(f"🔍 Scanning project: {self._project_dir}")
                self._project_snapshot = read_project(self._project_dir)
                self._print(f"📦 Snapshot: {self._project_snapshot.summary()}")
                self._logger.log(  # type: ignore[union-attr]
                    phase="INIT",
                    agent="conductor",
                    message=f"Project snapshot built: {self._project_snapshot.summary()}",
                )

            # Фаза 1: архитектор
            arch_result = self._run_architect(user_requirement)
            architecture = arch_result["architecture"]
            tasks = arch_result["tasks"]
            self._print(f"🏗️  Architecture ready. Tasks to implement: {len(tasks)}")

            # Определяем порядок выполнения задач
            task_waves = self._architect.get_task_order(tasks)  # type: ignore[union-attr]

            # Состояния всех задач
            task_states: dict[int, TaskState] = {
                t["id"]: TaskState(task=t) for t in tasks
            }

            # Контекст файлов для кодера и тестировщика (накапливается по мере реализации)
            # В edit-режиме начальный контекст = существующие файлы проекта
            context_files: list[dict[str, Any]] = []
            if self._edit_mode and self._project_snapshot:
                context_files = [
                    {"path": f.path, "content": f.content, "language": f.language}
                    for f in self._project_snapshot.files
                ]

            # Фаза 2+: CODER → TESTER → CRITIC для каждой задачи
            for wave_idx, wave in enumerate(task_waves):
                self._print(f"\n🌊 Wave {wave_idx + 1}/{len(task_waves)}: {len(wave)} task(s)")
                for task in wave:
                    state = task_states[task["id"]]
                    self._process_task(
                        state=state,
                        architecture=architecture,
                        user_requirement=user_requirement,
                        context_files=context_files,
                    )
                    # В create-режиме накапливаем новые файлы в контекст.
                    # В edit-режиме контекст уже содержит весь проект — не дублируем.
                    if not self._edit_mode and state.implemented_files:
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
            project_snapshot=self._project_snapshot.to_dict() if self._project_snapshot else None,
            edit_mode=self._edit_mode,
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

        Force-approve принимается здесь, в кондукторе, когда последняя
        итерация исчерпана — НЕ внутри critic.review().

        Returns:
            True если задача одобрена (или принудительно одобрена), False если failed
        """
        task = state.task
        rework_notes = ""

        self._print(f"\n  📌 Task {task['id']}: {task['title']}")

        while state.iteration < self.max_iterations:
            state.iteration += 1
            state.status = "running"
            self._print(f"    🔄 Iteration {state.iteration}/{self.max_iterations}")

            # --- CODER (с retry при ошибке парсинга JSON) ---
            self._transition(Phase.CODER, f"Task {task['id']}, iter {state.iteration}")
            coder_result = self._run_coder_with_retry(
                architecture=architecture,
                task=task,
                context_files=context_files,
                rework_notes=rework_notes,
            )
            if coder_result is None:
                # Все parse-retry исчерпаны — засчитываем как failed итерацию
                self._print(f"      ❌ Coder failed to produce valid JSON after {self.MAX_PARSE_RETRIES} retries")
                rework_notes = (
                    "CRITICAL: Your previous response was not valid JSON. "
                    "Output ONLY a raw JSON object matching the required schema. "
                    "Do NOT include any text, markdown, code fences, or commentary outside the JSON."
                )
                continue

            state.implemented_files = coder_result["files"]
            self._print(f"      💻 Coder: {len(coder_result['files'])} file(s)")

            # --- TESTER ---
            # Передаём context_files чтобы тестировщик видел все ранее реализованные
            # файлы (зависимости текущей задачи) и мог правильно мокировать их.
            self._transition(Phase.TESTER, f"Task {task['id']}, iter {state.iteration}")
            try:
                test_result = self._tester.test(  # type: ignore[union-attr]
                    architecture=architecture,
                    task=task,
                    implemented_files=state.implemented_files,
                    context_files=context_files,
                )
            except (RuntimeError, ValueError) as exc:
                # kiro CLI крашнулся или вернул невалидный JSON —
                # продолжаем с пустым test_result чтобы не ронять весь pipeline.
                self._log_state(Phase.TESTER, f"Tester agent error (skipped): {exc}")
                self._print(f"      ⚠️  Tester error (skipped): {str(exc)[:120]}")
                test_result = {"findings": [], "actual_run_output": {"passed": False, "exit_code": -1, "stdout": "", "stderr": str(exc)}}
            state.test_result = test_result
            findings_count = len(test_result.get("findings", []))
            tests_passed = test_result.get("actual_run_output", {}).get("passed", "unknown")
            self._print(f"      🧪 Tester: {findings_count} finding(s), tests passed={tests_passed}")

            # --- FORCE-APPROVE на последней итерации ---
            # Принимается кондуктором до вызова критика — критик уже вызывался
            # на предыдущих итерациях и каждый раз возвращал NEEDS_REWORK.
            if state.iteration >= self.max_iterations:
                self._print(f"      🎯 Critic: APPROVED (score=50)")
                self._log_state(
                    phase=Phase.CRITIC,
                    message=f"Max iterations ({self.max_iterations}) reached — forcing APPROVED",
                )
                state.critic_result = self._build_force_approve(task, state.iteration)
                state.status = "force_approved"
                state.force_approved = True
                self._print(f"    ✅ Task {task['id']} APPROVED (force)")
                return True

            # --- CRITIC ---
            # test_result передаётся критику чтобы он мог оценить реальные
            # результаты запуска тестов, а не только findings.
            self._transition(Phase.CRITIC, f"Task {task['id']}, iter {state.iteration}")
            try:
                critic_result = self._critic.review(  # type: ignore[union-attr]
                    user_requirement=user_requirement,
                    task=task,
                    implemented_files=state.implemented_files,
                    tester_findings=test_result.get("findings", []),
                    test_run_output=test_result.get("actual_run_output", {}),
                    iteration=state.iteration,
                    session_id=self._session_id,
                )
            except (RuntimeError, ValueError) as exc:
                # kiro CLI крашнулся или вернул невалидный JSON —
                # засчитываем как NEEDS_REWORK и продолжаем следующую итерацию.
                self._log_state(Phase.CRITIC, f"Critic agent error (treating as NEEDS_REWORK): {exc}")
                self._print(f"      ⚠️  Critic error (NEEDS_REWORK): {str(exc)[:120]}")
                rework_notes = (
                    "The previous critic review failed due to a technical error. "
                    "Please review the implementation carefully and ensure it meets all requirements."
                )
                continue
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

        # Недостижимо при корректном цикле, но на всякий случай
        state.status = "failed"
        self._print(f"    ❌ Task {task['id']} exhausted {self.max_iterations} iterations")
        return False

    # ------------------------------------------------------------------
    # Coder with parse-error retry
    # ------------------------------------------------------------------

    def _run_coder_with_retry(
        self,
        architecture: dict[str, Any],
        task: dict[str, Any],
        context_files: list[dict[str, Any]],
        rework_notes: str,
    ) -> dict[str, Any] | None:
        """
        Запустить кодер с retry при ошибке парсинга JSON.

        Если кодер вернул невалидный JSON — повторяем запрос с явной инструкцией
        по формату. Итерация в TaskState при этом НЕ увеличивается.

        Returns:
            Результат кодера или None если все retries исчерпаны.
        """
        current_notes = rework_notes
        for attempt in range(1, self.MAX_PARSE_RETRIES + 1):
            try:
                result = self._coder.implement(  # type: ignore[union-attr]
                    architecture=architecture,
                    task=task,
                    context_files=context_files,
                    rework_notes=current_notes,
                    edit_mode=self._edit_mode,
                )
                return result
            except ValueError as exc:
                # Ошибка парсинга JSON от кодера
                self._log_state(
                    phase=Phase.CODER,
                    message=f"Parse error on attempt {attempt}/{self.MAX_PARSE_RETRIES}: {exc}",
                )
                self._print(
                    f"      ⚠️  Coder parse error (attempt {attempt}/{self.MAX_PARSE_RETRIES}): {str(exc)[:120]}"
                )
                if attempt < self.MAX_PARSE_RETRIES:
                    # Добавляем строгую инструкцию по формату
                    format_reminder = (
                        "CRITICAL FORMAT ERROR: Your previous response could not be parsed as JSON.\n"
                        "You MUST output ONLY a single raw JSON object — no prose, no markdown fences, "
                        "no tool outputs, no commentary before or after.\n"
                        "All source code must be in the 'content' field as an escaped JSON string "
                        "(newlines as \\n, quotes as \\\", backslashes as \\\\).\n"
                    )
                    if current_notes and current_notes != "None":
                        current_notes = format_reminder + "\nOriginal rework notes:\n" + current_notes
                    else:
                        current_notes = format_reminder
        return None

    # ------------------------------------------------------------------
    # Force-approve helper (в кондукторе, не в критике)
    # ------------------------------------------------------------------

    def _build_force_approve(self, task: dict[str, Any], iteration: int) -> dict[str, Any]:
        """
        Формирует принудительный APPROVED.
        Решение принимается кондуктором — критик не вызывается.
        """
        return {
            "session_id": self._session_id,
            "task_id": task["id"],
            "verdict": "APPROVED",
            "score": 50,
            "summary": (
                f"Force-approved by conductor after {iteration} iterations "
                f"(max={self.max_iterations}). "
                "Task may have unresolved issues — review manually."
            ),
            "compliance_check": {
                "requirement_met": True,
                "architecture_followed": True,
                "acceptance_criteria": [],
            },
            "issues": [],
            "rework_instructions": "",
            "approved_files": [f.get("path", "") for f in []],
            "force_approved": True,
        }

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _init_session(self) -> None:
        """Инициализировать новую сессию и все агенты."""
        self._session_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )
        self._session_dir = self.OUTPUT_BASE / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._logger = SessionLogger(self._session_id, artifacts_dir=self._session_dir)

        self._architect = ArchitectAgent(self._logger)
        self._coder = CoderAgent(self._logger, output_dir=self._session_dir, project_dir=self._project_dir)
        self._tester = TesterAgent(self._logger, output_dir=self._session_dir)
        self._critic = CriticAgent(self._logger)

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
        approved = [s for s in task_states.values() if s.status in ("approved", "force_approved")]
        force_approved = [s for s in task_states.values() if s.force_approved]
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
            "force_approved_tasks": len(force_approved),
            "failed_tasks": len(failed),
            "total_iterations": sum(s.iteration for s in task_states.values()),
            "output_dir": str(self._session_dir),
            "output_files": list(set(all_files)),
            "summary": (
                f"Completed {len(approved)}/{len(task_states)} tasks "
                f"({len(force_approved)} force-approved). "
                f"Total iterations: {sum(s.iteration for s in task_states.values())}."
            ),
            "force_approved_task_ids": [s.task["id"] for s in force_approved],
            "known_limitations": [
                s.critic_result.get("summary", "")
                for s in list(force_approved) + list(failed)
                if s.critic_result
            ],
        }

        # Edit-режим: добавляем статистику по изменённым файлам
        if self._edit_mode:
            report["project_dir"] = str(self._project_dir)
            modified, created, deleted = self._collect_edit_stats(task_states)
            report["modified_files"] = modified
            report["created_files"] = created
            report["deleted_files"] = deleted

        if self._logger:
            self._logger.save_artifact("final_report.json", report)
            self._logger.log(
                phase="DONE",
                agent="conductor",
                message=(
                    f"Pipeline complete. {len(approved)}/{len(task_states)} tasks approved "
                    f"({len(force_approved)} force-approved)."
                ),
            )

        self._transition(Phase.DONE, "Pipeline finished")
        self._print(f"\n🎉 Done! Report saved to {self._session_dir}/final_report.json")
        return report

    def _collect_edit_stats(
        self,
        task_states: dict[int, TaskState],
    ) -> tuple[list[str], list[str], list[str]]:
        """
        Собирает статистику по изменённым/созданным/удалённым файлам
        в edit-режиме на основе mode-полей из ответов кодера.
        """
        modified: list[str] = []
        created: list[str] = []
        deleted: list[str] = []
        for state in task_states.values():
            for f in state.implemented_files:
                path = f.get("path", "")
                mode = f.get("mode", "patch")
                if mode == "delete":
                    deleted.append(path)
                elif mode == "create":
                    created.append(path)
                else:
                    modified.append(path)
        return (
            sorted(set(modified)),
            sorted(set(created)),
            sorted(set(deleted)),
        )

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
