"""
src/agents/coder.py
Агент-кодер: реализует конкретную задачу по спецификации архитектора.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agents.base import BaseAgent
from src.utils.session_logger import SessionLogger


class CoderAgent(BaseAgent):
    """
    Принимает одну задачу из очереди архитектора и возвращает готовые файлы с кодом.

    Output schema:
    {
        "task_id": int,
        "files": [ { "path", "content", "language" } ],
        "notes": str,
        "known_limitations": [ str ]
    }
    """

    template_name = "coder"
    kiro_role = "kiro_default"
    timeout = 480

    def __init__(
        self,
        session_logger: SessionLogger,
        output_dir: Path,
    ) -> None:
        super().__init__(session_logger)
        self._output_dir = output_dir

    def implement(
        self,
        architecture: dict[str, Any],
        task: dict[str, Any],
        context_files: list[dict[str, Any]] | None = None,
        rework_notes: str = "",
    ) -> dict[str, Any]:
        """
        Реализовать одну задачу.

        Args:
            architecture: архитектурный документ от архитектора
            task: конкретная задача для реализации
            context_files: список уже реализованных файлов (для контекста)
            rework_notes: инструкции по доработке от критика (если есть)

        Returns:
            Словарь с ключом 'files' и метаданными
        """
        result = self.run(
            architecture=json.dumps(architecture, ensure_ascii=False, indent=2),
            task=json.dumps(task, ensure_ascii=False, indent=2),
            context_files=json.dumps(context_files or [], ensure_ascii=False, indent=2),
            rework_notes=rework_notes or "None",
        )

        # Сохраняем все файлы в output-директорию
        saved_paths = self._save_files(result["files"])
        result["saved_paths"] = [str(p) for p in saved_paths]

        self.logger.save_artifact(f"coder_task_{task['id']}.json", result)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_files(self, files: list[dict[str, Any]]) -> list[Path]:
        """Записывает файлы из ответа кодера в файловую систему."""
        paths: list[Path] = []
        for file_info in files:
            rel_path = file_info.get("path", "unknown_file.py")
            content = file_info.get("content", "")
            full_path = self._output_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            paths.append(full_path)
            self.logger.log(
                phase="CODER",
                agent="coder",
                message=f"Saved file: {rel_path}",
            )
        return paths

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, result: dict[str, Any]) -> None:
        if "task_id" not in result:
            raise ValueError("Coder response missing 'task_id'")
        if "files" not in result:
            raise ValueError("Coder response missing 'files'")
        if not isinstance(result["files"], list) or len(result["files"]) == 0:
            raise ValueError("Coder must produce at least one file")
        for i, f in enumerate(result["files"]):
            if "path" not in f:
                raise ValueError(f"File #{i} missing 'path'")
            if "content" not in f:
                raise ValueError(f"File #{i} missing 'content'")
