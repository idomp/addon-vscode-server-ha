#!/usr/bin/env python3
"""Patch Copilot run_in_terminal to skip file-scheme provider requirements.

This script searches the built VS Code (or VS Code Server) JavaScript bundles
for the `run_in_terminal` tool implementation and rewrites any `fileService.stat`
validation near that tool to be conditional on a provider being available.

It also rewrites eager `URI.file(<workspace>.uri.fsPath)` constructions near the
tool to preserve the original workspace URI (avoiding forced `file` schemes in
web builds without a `file` provider).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Tuple


SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/usr/lib/code"),
    Path("/usr/lib/vscode-server"),
    Path("/opt/vscode-server"),
)


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Yield JS-like files under the provided root."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath, name)
            if path.suffix.lower() in {".js", ".mjs", ".cjs"}:
                yield path


def compute_replacements(text: str) -> Tuple[List[Tuple[int, int, str]], bool]:
    """Compute textual replacements for a single file.

    Returns a tuple containing:
    - A list of (start, end, replacement) tuples.
    - A bool indicating whether the file is relevant (contains run_in_terminal).
    """
    run_positions = [m.start() for m in re.finditer(r"run_in_terminal", text)]
    if not run_positions:
        return [], False

    replacements: List[Tuple[int, int, str]] = []

    # Guard fileService.stat usage near the tool registration/handler.
    stat_pattern = re.compile(
        r"await\s+((?:[A-Za-z0-9_$]+\.)*[A-Za-z0-9_$]+)\.stat\(([^)]+)\);"
    )
    for match in stat_pattern.finditer(text):
        start, end = match.span()
        if any(pos - 800 <= start <= pos + 4000 for pos in run_positions):
            service = match.group(1)
            arg = match.group(2)
            replacement = (
                f"await ((({service}.hasProvider?.({arg})) ?? "
                f"({service}.canHandleResource?.({arg})) ?? false) "
                f"? {service}.stat({arg}) : Promise.resolve());"
            )
            replacements.append((start, end, replacement))

    # Avoid forced file-scheme URIs near the tool.
    uri_pattern = re.compile(
        r"URI\.file\(\s*([A-Za-z0-9_$]+)\.uri\.fsPath\s*\)"
    )
    for match in uri_pattern.finditer(text):
        start, end = match.span()
        if any(pos - 800 <= start <= pos + 4000 for pos in run_positions):
            workspace_var = match.group(1)
            replacements.append((start, end, f"{workspace_var}.uri"))

    return replacements, True


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    # Apply replacements from the end to preserve offsets.
    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def patch_file(path: Path) -> bool:
    original = path.read_text(encoding="utf-8", errors="ignore")
    replacements, relevant = compute_replacements(original)
    if not relevant or not replacements:
        return False

    patched = apply_replacements(original, replacements)
    if patched != original:
        path.write_text(patched, encoding="utf-8")
        return True
    return False


def main() -> int:
    patched_files: list[str] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for file_path in iter_candidate_files(root):
            try:
                if patch_file(file_path):
                    patched_files.append(str(file_path))
            except Exception:
                # Continue patching other files even if one fails.
                continue

    if patched_files:
        print("Patched run_in_terminal provider checks in:")
        for path in patched_files:
            print(f" - {path}")
    else:
        print("No run_in_terminal occurrences patched (none found or already patched).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
