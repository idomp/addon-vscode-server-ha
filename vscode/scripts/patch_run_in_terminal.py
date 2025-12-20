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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


MARKER = "/* patched: run_in_terminal */"


SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/usr/lib/code"),
    Path("/usr/lib/vscode-server"),
    Path("/opt/vscode-server"),
    Path("/usr/share/code"),
)


@dataclass
class PatchResult:
    path: Path
    relevant: bool
    modified: bool
    marker_present: bool
    error: Exception | None = None


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Yield JS-like files under the provided root."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath, name)
            if path.suffix.lower() in {".js", ".mjs", ".cjs"}:
                yield path


def compute_replacements(text: str) -> Tuple[List[Tuple[int, int, str]], List[int]]:
    """Compute textual replacements for a single file."""
    run_positions = [m.start() for m in re.finditer(r"run_in_terminal", text)]
    if not run_positions:
        return [], []

    replacements: List[Tuple[int, int, str]] = []

    # Guard fileService stat/exists/resolve usage near the tool registration/handler.
    guarded_pattern = re.compile(
        r"await\s+((?:[A-Za-z0-9_$]+\.)*[A-Za-z0-9_$]+)\.(stat|exists|resolve)\(([^)]+)\);"
    )
    for match in guarded_pattern.finditer(text):
        start, end = match.span()
        if any(pos - 800 <= start <= pos + 4000 for pos in run_positions):
            service, method, arg = match.groups()
            fallback = "Promise.resolve(true)" if method == "exists" else "Promise.resolve()"
            replacement = (
                f"await ((({service}.hasProvider?.({arg})) ?? "
                f"({service}.canHandleResource?.({arg})) ?? false) "
                f"? {service}.{method}({arg}) : {fallback});"
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

    return replacements, run_positions


def is_already_guarded(text: str, run_positions: List[int]) -> bool:
    guard_pattern = re.compile(r"hasProvider\?\.|canHandleResource\?\.")
    for position in run_positions:
        window = text[max(0, position - 800) : position + 4000]
        if guard_pattern.search(window):
            return True
    return False


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    # Apply replacements from the end to preserve offsets.
    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def patch_file(path: Path) -> PatchResult:
    original = path.read_text(encoding="utf-8", errors="ignore")
    replacements, run_positions = compute_replacements(original)
    relevant = bool(run_positions)
    already_guarded = is_already_guarded(original, run_positions) if relevant else False
    marker_present = MARKER in original
    modified = False
    updated_text = original

    guard_applied = False
    if relevant and replacements:
        updated_text = apply_replacements(original, replacements)
        modified = updated_text != original
        guard_applied = True
    elif relevant and already_guarded:
        guard_applied = True

    if relevant and not guard_applied:
        return PatchResult(
            path=path,
            relevant=True,
            modified=False,
            marker_present=marker_present,
            error=RuntimeError("run_in_terminal located but no guard patterns found"),
        )

    if relevant and MARKER not in updated_text:
        updated_text = f"{updated_text.rstrip()}\n{MARKER}\n"
        modified = True
        marker_present = True
    elif relevant:
        marker_present = MARKER in updated_text

    if relevant and guard_applied and modified:
        path.write_text(updated_text, encoding="utf-8")

    return PatchResult(
        path=path,
        relevant=relevant,
        modified=modified,
        marker_present=marker_present,
    )


def main() -> int:
    results: list[PatchResult] = []

    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue

        for file_path in iter_candidate_files(root):
            try:
                results.append(patch_file(file_path))
            except Exception as exc:  # pragma: no cover - defensive logging
                results.append(PatchResult(file_path, False, False, False, exc))

    relevant_results = [result for result in results if result.relevant]
    if not relevant_results:
        print("ERROR: No run_in_terminal occurrences found under expected roots.")
        return 1

    patched_any = False
    print("Patched run_in_terminal provider checks in:")
    for result in relevant_results:
        status_parts = []
        if result.modified:
            status_parts.append("modified")
        else:
            status_parts.append("unchanged")

        if result.marker_present:
            status_parts.append("marker=present")
        else:
            status_parts.append("marker=missing")

        if result.error:
            status_parts.append(f"error={result.error}")

        print(f" - {result.path} ({', '.join(status_parts)})")
        patched_any = patched_any or result.modified

    failures = [
        result
        for result in relevant_results
        if result.error is not None or not result.marker_present
    ]
    if failures:
        print("ERROR: Marker verification failed for the bundles above.")
        return 1

    if not patched_any:
        print("No changes were necessary; files already contained guarded logic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
