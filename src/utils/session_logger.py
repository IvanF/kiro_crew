"""
src/utils/session_logger.py
Логирует все переходы состояний и артефакты агентов в JSON-файл сессии.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "sessions"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class SessionLogger:
    """Потокобезопасный логгер сессии. Каждая сессия — отдельный JSON-файл."""

    def __init__(self, session_id: str, artifacts_dir: Path | None = None) -> None:
        self.session_id = session_id
        self._log_path = OUTPUT_DIR / f"{session_id}.json"
        # Директория для артефактов: явно переданная или рядом с лог-файлом
        self._artifacts_dir: Path = artifacts_dir or (OUTPUT_DIR / session_id)
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict[str, Any]] = []
        self._logger = logging.getLogger(f"session.{session_id}")
        self._save()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        phase: str,
        agent: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Добавить запись в лог сессии."""
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "agent": agent,
            "message": message,
        }
        if payload:
            entry["payload"] = payload
        self._entries.append(entry)
        self._save()
        self._logger.info("[%s/%s] %s", phase, agent, message)

    def save_artifact(self, name: str, data: Any) -> Path:
        """Сохранить артефакт (код, тесты, отчёт) в директорию сессии."""
        path = self._artifacts_dir / name
        if isinstance(data, (dict, list)):
            out = path.with_suffix(".json")
            out.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return out
        else:
            path.write_text(str(data), encoding="utf-8")
            return path

    def get_log(self) -> list[dict[str, Any]]:
        """Вернуть все записи лога."""
        return list(self._entries)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save(self) -> None:
        self._log_path.write_text(
            json.dumps(
                {"session_id": self.session_id, "entries": self._entries},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
