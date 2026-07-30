"""
src/utils/project_reader.py
Сканирует существующий проект и строит snapshot для передачи агентам.

Snapshot — это список файлов с их содержимым, отфильтрованный по расширению
и размеру, с возможностью исключения директорий (node_modules, .git и т.д.).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Расширения файлов, которые включаются в snapshot по умолчанию
DEFAULT_INCLUDE_EXTENSIONS = {
    # Web / JS
    ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".vue", ".svelte",
    # Python
    ".py", ".pyi",
    # Backend / configs
    ".go", ".rs", ".java", ".kt", ".cs", ".cpp", ".c", ".h",
    ".rb", ".php", ".swift",
    # Data / config
    ".json", ".yaml", ".yml", ".toml", ".ini", ".env.example",
    ".xml", ".sql",
    # Docs / markup
    ".md", ".rst", ".txt",
    # Shell
    ".sh", ".bash", ".zsh",
}

# Директории, которые всегда пропускаются
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".svn", ".hg",
    "node_modules", ".pnp",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env",
    "dist", "build", "out", ".next", ".nuxt", ".output",
    ".idea", ".vscode",
    "coverage", ".nyc_output",
    "eggs", "*.egg-info",
}

# Файлы, которые всегда пропускаются
DEFAULT_EXCLUDE_FILES = {
    ".DS_Store", "Thumbs.db",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock",
    "*.pyc", "*.pyo",
}

# Максимальный размер одного файла для включения в snapshot (байты)
MAX_FILE_SIZE = 100_000  # 100 КБ

# Максимальное суммарное число символов snapshot (ограничение контекста LLM)
MAX_SNAPSHOT_CHARS = 200_000


@dataclass
class ProjectFile:
    """Один файл проекта в snapshot."""

    path: str           # относительный путь от корня проекта
    content: str        # содержимое файла (строка)
    language: str       # определённый язык/тип
    size_bytes: int     # размер в байтах
    truncated: bool = False  # True если содержимое обрезано по MAX_FILE_SIZE


@dataclass
class ProjectSnapshot:
    """Полный snapshot существующего проекта."""

    root: str                               # абсолютный путь к корню проекта
    files: list[ProjectFile] = field(default_factory=list)
    total_files_scanned: int = 0            # сколько файлов прошли фильтр
    total_files_skipped: int = 0            # сколько пропущено (большие, бинарные)
    truncated: bool = False                 # True если snapshot обрезан по MAX_SNAPSHOT_CHARS

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать snapshot в dict для JSON-передачи агентам."""
        return {
            "root": self.root,
            "total_files": self.total_files_scanned,
            "skipped_files": self.total_files_skipped,
            "truncated": self.truncated,
            "files": [
                {
                    "path": f.path,
                    "language": f.language,
                    "size_bytes": f.size_bytes,
                    "truncated": f.truncated,
                    "content": f.content,
                }
                for f in self.files
            ],
        }

    def summary(self) -> str:
        """Короткое описание snapshot для логов."""
        return (
            f"{len(self.files)} files ({self.total_files_scanned} scanned, "
            f"{self.total_files_skipped} skipped"
            + (", snapshot truncated" if self.truncated else "")
            + ")"
        )


def read_project(
    project_dir: str | Path,
    include_extensions: set[str] | None = None,
    exclude_dirs: set[str] | None = None,
    exclude_files: set[str] | None = None,
    max_file_size: int = MAX_FILE_SIZE,
    max_snapshot_chars: int = MAX_SNAPSHOT_CHARS,
) -> ProjectSnapshot:
    """
    Сканирует директорию проекта и строит snapshot.

    Args:
        project_dir: путь к корню проекта
        include_extensions: расширения файлов для включения (None = DEFAULT_INCLUDE_EXTENSIONS)
        exclude_dirs: имена директорий для пропуска (None = DEFAULT_EXCLUDE_DIRS)
        exclude_files: имена файлов для пропуска (None = DEFAULT_EXCLUDE_FILES)
        max_file_size: максимальный размер одного файла в байтах
        max_snapshot_chars: максимальное суммарное число символов в snapshot

    Returns:
        ProjectSnapshot с отфильтрованными файлами

    Raises:
        FileNotFoundError: если project_dir не существует
        NotADirectoryError: если project_dir — не директория
    """
    root = Path(project_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"project_dir not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"project_dir is not a directory: {root}")

    inc_exts = include_extensions if include_extensions is not None else DEFAULT_INCLUDE_EXTENSIONS
    exc_dirs = exclude_dirs if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
    exc_files = exclude_files if exclude_files is not None else DEFAULT_EXCLUDE_FILES

    snapshot = ProjectSnapshot(root=str(root))
    total_chars = 0

    for abs_path in _walk_files(root, exc_dirs):
        rel_path = abs_path.relative_to(root)
        rel_str = rel_path.as_posix()

        # Фильтр по имени файла
        if _matches_any(abs_path.name, exc_files):
            snapshot.total_files_skipped += 1
            continue

        # Фильтр по расширению
        if abs_path.suffix.lower() not in inc_exts:
            snapshot.total_files_skipped += 1
            continue

        # Фильтр по размеру
        try:
            size = abs_path.stat().st_size
        except OSError:
            snapshot.total_files_skipped += 1
            continue

        if size == 0:
            snapshot.total_files_skipped += 1
            continue

        # Читаем содержимое
        truncated_file = False
        try:
            if size > max_file_size:
                # Читаем только первые max_file_size байт
                raw = abs_path.read_bytes()[:max_file_size]
                content = raw.decode("utf-8", errors="replace")
                truncated_file = True
            else:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            snapshot.total_files_skipped += 1
            continue

        snapshot.total_files_scanned += 1

        # Ограничение суммарного объёма snapshot
        if total_chars + len(content) > max_snapshot_chars:
            snapshot.truncated = True
            # Включаем файл частично если в snapshot ещё есть место
            remaining = max_snapshot_chars - total_chars
            if remaining > 200:
                content = content[:remaining] + "\n... [snapshot limit reached, file truncated]"
                truncated_file = True
                total_chars = max_snapshot_chars
                snapshot.files.append(ProjectFile(
                    path=rel_str,
                    content=content,
                    language=_detect_language(abs_path),
                    size_bytes=size,
                    truncated=truncated_file,
                ))
            break

        total_chars += len(content)
        snapshot.files.append(ProjectFile(
            path=rel_str,
            content=content,
            language=_detect_language(abs_path),
            size_bytes=size,
            truncated=truncated_file,
        ))

    return snapshot


def get_file_content(project_dir: str | Path, rel_path: str) -> str | None:
    """
    Читает содержимое конкретного файла из проекта.

    Args:
        project_dir: корень проекта
        rel_path: относительный путь к файлу (как в snapshot)

    Returns:
        Содержимое файла или None если файл не существует
    """
    full_path = Path(project_dir) / rel_path
    if not full_path.exists() or not full_path.is_file():
        return None
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _walk_files(root: Path, exclude_dirs: set[str]) -> list[Path]:
    """
    Обходит дерево директорий, исключая указанные директории.
    Возвращает отсортированный список файлов (детерминированный порядок).
    """
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Фильтруем поддиректории in-place (os.walk учитывает это)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in exclude_dirs and not d.startswith(".")
        )
        for fname in sorted(filenames):
            result.append(Path(dirpath) / fname)
    return result


def _matches_any(name: str, patterns: set[str]) -> bool:
    """Проверяет, совпадает ли имя файла с одним из паттернов (поддержка *)."""
    import fnmatch
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
    ".vue": "vue", ".svelte": "svelte",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".h": "c",
    ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".xml": "xml",
    ".sql": "sql", ".md": "markdown", ".rst": "rst",
    ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
    ".txt": "text",
}


def _detect_language(path: Path) -> str:
    """Определяет язык/тип файла по расширению."""
    return _EXT_TO_LANGUAGE.get(path.suffix.lower(), "text")
