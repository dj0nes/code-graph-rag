from __future__ import annotations

import io
from pathlib import Path

from loguru import logger


class CPreprocessor:
    def __init__(
        self,
        include_dirs: list[Path] | None = None,
        defines: dict[str, str] | None = None,
    ) -> None:
        self.include_dirs: list[Path] = include_dirs or []
        self.defines: dict[str, str] = defines or {}

    def expand(self, filepath: Path, source_bytes: bytes) -> bytes:
        try:
            from pcpp import Action, OutputDirective, Preprocessor  # type: ignore[import-untyped]
        except ImportError:
            logger.debug(
                "pcpp not installed; skipping C preprocessing for {}", filepath
            )
            return source_bytes

        try:
            # (H) Subclass to silence pcpp's stderr output and suppress missing-include errors.
            class _PP(Preprocessor):  # type: ignore[misc]
                def on_error(self, file: str, line: int, msg: str) -> None:
                    logger.debug("pcpp: {}:{}: {}", file, line, msg)

                def on_include_not_found(
                    self,
                    is_malformed: bool,
                    is_system_include: bool,
                    curfile: str,
                    includepath: str,
                ) -> None:
                    raise OutputDirective(Action.IgnoreAndPassThrough)

            pp = _PP()
            pp.line_directive = None

            for path in self.include_dirs:
                pp.add_path(str(path))

            for key, val in self.defines.items():
                pp.define(f"{key} {val}".strip())

            source_text = source_bytes.decode("utf-8", errors="replace")
            pp.parse(source_text, str(filepath))

            output = io.StringIO()
            pp.write(output)
            return output.getvalue().encode("utf-8")

        except Exception:
            logger.debug(
                "C preprocessing failed for {}; falling back to raw source", filepath
            )
            return source_bytes

    @classmethod
    def from_settings(cls, repo_path: Path) -> CPreprocessor | None:
        """The preprocessor the CGR_C_* settings ask for, or None when off."""
        from ..config import settings

        if not settings.C_PREPROCESS:
            return None
        include_dirs = [d.strip() for d in settings.C_INCLUDE_DIRS.split(",")]
        defines: dict[str, str] = {}
        for defn in settings.C_DEFINES.split(","):
            key, _, val = defn.partition("=")
            if key.strip():
                defines[key.strip()] = val.strip()
        return cls.from_repo(
            repo_path,
            extra_include_dirs=[d for d in include_dirs if d],
            defines=defines,
        )

    @classmethod
    def from_repo(
        cls,
        repo_path: Path,
        extra_include_dirs: list[str] | None = None,
        defines: dict[str, str] | None = None,
    ) -> CPreprocessor:
        include_dirs: list[Path] = []

        for d in extra_include_dirs or []:
            candidate = repo_path / d
            if candidate.is_dir():
                include_dirs.append(candidate)

        cc_json = repo_path / "compile_commands.json"
        if cc_json.is_file():
            include_dirs.extend(_parse_compile_commands(cc_json))

        heuristic_names = {"inc", "include", "headers", "ddi_inc"}
        for candidate in repo_path.iterdir():
            if candidate.is_dir() and candidate.name.lower() in heuristic_names:
                if candidate not in include_dirs:
                    include_dirs.append(candidate)

        logger.debug(
            "CPreprocessor: {} include dirs detected for {}",
            len(include_dirs),
            repo_path,
        )
        return cls(include_dirs=include_dirs, defines=defines)


def _parse_compile_commands(cc_json: Path) -> list[Path]:
    import json

    seen: set[Path] = set()
    result: list[Path] = []
    try:
        data = json.loads(cc_json.read_text(encoding="utf-8"))
        for entry in data:
            cmd: str = entry.get("command", "") or " ".join(entry.get("arguments", []))
            parts = cmd.split()
            for i, part in enumerate(parts):
                if part == "-I" and i + 1 < len(parts):
                    p = Path(parts[i + 1])
                elif part.startswith("-I"):
                    p = Path(part[2:])
                else:
                    continue
                if p not in seen:
                    seen.add(p)
                    result.append(p)
    except Exception as exc:
        logger.debug("Could not parse compile_commands.json: {}", exc)
    return result
