"""
src/agents/coder.py
Агент-кодер: реализует конкретную задачу по спецификации архитектора.

Поддерживает два режима:
  create (по умолчанию) — записывает файлы в output_dir (сессионную директорию).
  edit   (edit_mode=True) — записывает файлы обратно в project_dir с учётом
          поля mode в каждом файле: patch (перезапись), create (новый файл),
          delete (удаление файла).
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

    Режим create — output schema:
    {
        "task_id": int,
        "files": [ { "path", "content", "language" } ],
        "notes": str,
        "known_limitations": [ str ]
    }

    Режим edit — output schema (расширенный):
    {
        "task_id": int,
        "files": [
            {
                "path": "<relative path from project root>",
                "mode": "patch" | "create" | "delete",
                "content": "<full new content (for patch/create), empty string for delete>",
                "language": "<language>"
            }
        ],
        "notes": str,
        "known_limitations": [ str ]
    }
    """

    template_name = "coder"
    kiro_role = "kiro_default"
    timeout = 480
    response_keys = ["task_id", "files"]

    def __init__(
        self,
        session_logger: SessionLogger,
        output_dir: Path,
        project_dir: Path | None = None,
    ) -> None:
        super().__init__(session_logger)
        self._output_dir = output_dir
        self._project_dir = project_dir

    def implement(
        self,
        architecture: dict[str, Any],
        task: dict[str, Any],
        context_files: list[dict[str, Any]] | None = None,
        rework_notes: str = "",
        edit_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Реализовать одну задачу.

        Args:
            architecture: архитектурный документ от архитектора
            task: конкретная задача для реализации
            context_files: список уже реализованных / существующих файлов (для контекста)
            rework_notes: инструкции по доработке от критика (если есть)
            edit_mode: True — edit-режим (используется coder_edit.md промт)

        Returns:
            Словарь с ключом 'files' и метаданными
        """
        # В edit-режиме используем отдельный промт
        original_template = self.template_name
        if edit_mode:
            self.template_name = "coder_edit"

        try:
            result = self.run(
                architecture=json.dumps(architecture, ensure_ascii=False, indent=2),
                task=json.dumps(task, ensure_ascii=False, indent=2),
                context_files=json.dumps(context_files or [], ensure_ascii=False, indent=2),
                rework_notes=rework_notes or "None",
            )
        finally:
            # Восстанавливаем имя шаблона в любом случае
            self.template_name = original_template

        # Сохраняем файлы: в edit-режиме пишем в project_dir, иначе в output_dir
        if edit_mode and self._project_dir:
            saved_paths = self._save_files_edit(result["files"], self._project_dir)
        else:
            saved_paths = self._save_files(result["files"])

        result["saved_paths"] = [str(p) for p in saved_paths]
        self.logger.save_artifact(f"coder_task_{task['id']}.json", result)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_files(self, files: list[dict[str, Any]]) -> list[Path]:
        """Записывает файлы из ответа кодера в output_dir (create-режим)."""
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

    def _save_files_edit(
        self,
        files: list[dict[str, Any]],
        project_dir: Path,
    ) -> list[Path]:
        """
        Записывает правки обратно в project_dir (edit-режим).
        Поддерживает mode: patch (перезапись), create (новый файл), delete (удаление).
        Оригиналы перед перезаписью сохраняются в output_dir как backup.
        """
        paths: list[Path] = []
        for file_info in files:
            rel_path = file_info.get("path", "")
            if not rel_path:
                continue
            mode = file_info.get("mode", "patch")
            content = file_info.get("content", "")
            full_path = project_dir / rel_path

            if mode == "delete":
                if full_path.exists():
                    # Сохраняем backup перед удалением
                    self._backup_file(full_path, rel_path)
                    full_path.unlink()
                    self.logger.log(
                        phase="CODER",
                        agent="coder",
                        message=f"Deleted file: {rel_path}",
                    )
                continue

            if mode in ("patch", "create"):
                # Backup оригинала если файл уже существует
                if full_path.exists():
                    self._backup_file(full_path, rel_path)
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                paths.append(full_path)
                action = "Patched" if mode == "patch" else "Created"
                self.logger.log(
                    phase="CODER",
                    agent="coder",
                    message=f"{action} file: {rel_path}",
                )

        return paths

    def _backup_file(self, full_path: Path, rel_path: str) -> None:
        """Сохраняет оригинал файла в output_dir/backup/ перед изменением."""
        try:
            backup_path = self._output_dir / "backup" / rel_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(full_path.read_bytes())
        except OSError:
            pass  # Backup не критичен — не прерываем основной flow

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
