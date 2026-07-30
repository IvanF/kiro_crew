"""
src/agents/tester.py
Агент-тестировщик: покрывает реализованный код тестами,
ищет слабые места, запускает тесты и отчитывается о результатах.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.agents.base import BaseAgent
from src.utils.session_logger import SessionLogger


class TesterAgent(BaseAgent):
    """
    Принимает реализованный код и возвращает:
    - тестовые файлы с максимальным покрытием
    - команду для запуска тестов
    - список найденных проблем

    Output schema:
    {
        "task_id": int,
        "test_files": [ { "path", "content", "framework" } ],
        "test_run_command": str,
        "coverage_report": { "estimated_coverage", "uncovered_areas" },
        "findings": [ { "severity", "description", "test_case" } ]
    }
    """

    template_name = "tester"
    kiro_role = "kiro_default"
    timeout = 480
    response_keys = ["task_id", "test_files"]

    def __init__(
        self,
        session_logger: SessionLogger,
        output_dir: Path,
    ) -> None:
        super().__init__(session_logger)
        self._output_dir = output_dir

    def test(
        self,
        architecture: dict[str, Any],
        task: dict[str, Any],
        implemented_files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Создать и запустить тесты для реализованного кода.

        Args:
            architecture: архитектурный документ
            task: спецификация задачи
            implemented_files: список файлов от кодера (path + content)

        Returns:
            Полный отчёт тестирования
        """
        result = self.run(
            architecture=json.dumps(architecture, ensure_ascii=False, indent=2),
            task=json.dumps(task, ensure_ascii=False, indent=2),
            implemented_files=json.dumps(implemented_files, ensure_ascii=False, indent=2),
        )

        # Сохраняем тестовые файлы
        self._save_test_files(result.get("test_files", []))

        # Пытаемся запустить тесты и обогатить отчёт реальными результатами
        run_result = self._run_tests(result.get("test_run_command", ""))
        result["actual_run_output"] = run_result

        self.logger.save_artifact(f"tester_task_{task['id']}.json", result)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_test_files(self, test_files: list[dict[str, Any]]) -> None:
        """Записывает тестовые файлы в файловую систему."""
        for file_info in test_files:
            rel_path = file_info.get("path", "tests/unknown_test.py")
            content = file_info.get("content", "")
            full_path = self._output_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            self.logger.log(
                phase="TESTER",
                agent="tester",
                message=f"Saved test file: {rel_path}",
            )

    def _run_tests(self, command: str) -> dict[str, Any]:
        """
        Запускает тесты в изолированном процессе.

        Returns:
            Словарь с 'exit_code', 'stdout', 'stderr'
        """
        if not command:
            return {"exit_code": -1, "stdout": "", "stderr": "No test command provided"}

        # Базовая санация: разрешаем только известные тест-раннеры
        import shlex
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return {"exit_code": -1, "stdout": "", "stderr": f"Invalid command syntax: {e}"}

        ALLOWED_RUNNERS = {"pytest", "python", "python3", "npm", "npx", "jest", "vitest", "node"}
        if not parts or parts[0].split("/")[-1].split("\\")[-1] not in ALLOWED_RUNNERS:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Disallowed test runner: '{parts[0] if parts else ''}'. Allowed: {ALLOWED_RUNNERS}",
            }

        self.logger.log(
            phase="TESTER",
            agent="tester",
            message=f"Running tests: {command}",
        )
        try:
            proc = subprocess.run(
                command,
                shell=True,  # noqa: S602
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self._output_dir),
            )
            result = {
                "exit_code": proc.returncode,
                "stdout": proc.stdout[-3000:],  # обрезаем для компактности
                "stderr": proc.stderr[-1000:],
                "passed": proc.returncode == 0,
            }
            self.logger.log(
                phase="TESTER",
                agent="tester",
                message=f"Tests {'PASSED' if result['passed'] else 'FAILED'} (exit {proc.returncode})",
            )
            return result
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "Test run timed out after 120s", "passed": False}
        except Exception as exc:  # noqa: BLE001
            return {"exit_code": -1, "stdout": "", "stderr": str(exc), "passed": False}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, result: dict[str, Any]) -> None:
        if "task_id" not in result:
            raise ValueError("Tester response missing 'task_id'")
        if "test_files" not in result:
            raise ValueError("Tester response missing 'test_files'")
        if not isinstance(result["test_files"], list) or len(result["test_files"]) == 0:
            raise ValueError("Tester must produce at least one test file")
        if "findings" not in result:
            result["findings"] = []  # не обязательное поле — допускаем пустой список
