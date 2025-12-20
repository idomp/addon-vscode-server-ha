#!/usr/bin/env python3
"""Patch Copilot run_in_terminal to skip file-scheme provider requirements.

This script searches the built VS Code (or VS Code Server) JavaScript bundles
for the `run_in_terminal` tool implementation, rewrites any `fileService.stat`
validation near that tool to be conditional on a provider being available, and
replaces `*.file(<workspace>.uri.fsPath)` with `<workspace>.uri` to avoid forced
`file` schemes when no provider exists.

A marker comment `/* patched: run_in_terminal */` is inserted for every change
and the build fails if no markers were added.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/usr/lib/code"),
    Path("/usr/lib/vscode-server"),
    Path("/opt/vscode-server"),
)

WINDOW_BEFORE = 1600
WINDOW_AFTER = 6000

URI_PATTERN = re.compile(
    r"(?:[A-Za-z0-9_$]+)\.file\(\s*(?P<uri>[A-Za-z0-9_$]+)\.uri\.fsPath\s*\)"
)
PROVIDER_PATTERN = re.compile(
    r"(?P<prefix>await\s+)?(?P<service>(?:[A-Za-z0-9_$]+\.)*[A-Za-z0-9_$]+)\."
    r"(?P<method>stat|exists|resolve)\((?P<arg>[^)]+)\)"
)


@dataclass
class ReplacementSummary:
    uri_replacements: int = 0
    provider_guards: int = 0
    markers: int = 0


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Yield JS-like files under the provided root."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath, name)
            if "workbench" not in path.name.lower():
                continue
            if path.suffix.lower() in {".js", ".mjs", ".cjs"}:
                yield path


def merged_windows(positions: List[int], text_length: int) -> List[Tuple[int, int]]:
    """Merge overlapping windows around each run_in_terminal occurrence."""
    windows: List[Tuple[int, int]] = []
    for pos in positions:
        start = max(0, pos - WINDOW_BEFORE)
        end = min(text_length, pos + WINDOW_AFTER)
        if windows and start <= windows[-1][1]:
            prev_start, prev_end = windows[-1]
            windows[-1] = (prev_start, max(prev_end, end))
        else:
            windows.append((start, end))
    return windows


def compute_replacements(text: str) -> Tuple[List[Tuple[int, int, str]], bool, ReplacementSummary]:
    """Compute textual replacements for a single file.

    Returns a tuple containing:
    - A list of (start, end, replacement) tuples.
    - A bool indicating whether the file is relevant (contains run_in_terminal).
    - A ReplacementSummary with counts.
    """
    run_positions = [m.start() for m in re.finditer(r"run_in_terminal", text)]
    if not run_positions:
        return [], False, ReplacementSummary()

    replacements: List[Tuple[int, int, str]] = []
    summary = ReplacementSummary()
    windows = merged_windows(run_positions, len(text))
    seen_spans: set[Tuple[int, int]] = set()

    # Guard fileService stat/exists/resolve usage near the tool registration/handler.
    for start, end in windows:
        for match in PROVIDER_PATTERN.finditer(text, start, end):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            prefix = match.group("prefix") or ""
            service = match.group("service")
            method = match.group("method")
            arg = match.group("arg")
            fallback = "Promise.resolve(true)" if method == "exists" else "Promise.resolve()"
            provider_guard = (
                f"(({service}.hasProvider?.({arg})) ?? "
                f"({service}.canHandleResource?.({arg})) ?? false)"
            )
            replacement = (
                f"{prefix}/* patched: run_in_terminal */"
                f"({provider_guard} ? {service}.{method}({arg}) : {fallback})"
            )
            replacements.append((span[0], span[1], replacement))
            summary.provider_guards += 1
            summary.markers += 1

        # Avoid forced file-scheme URIs near the tool.
        for match in URI_PATTERN.finditer(text, start, end):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            workspace_var = match.group("uri")
            replacement = f"/* patched: run_in_terminal */{workspace_var}.uri"
            replacements.append((span[0], span[1], replacement))
            summary.uri_replacements += 1
            summary.markers += 1

    return replacements, True, summary


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    # Apply replacements from the end to preserve offsets.
    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def patch_file(path: Path) -> Tuple[bool, ReplacementSummary | None]:
    original = path.read_text(encoding="utf-8", errors="ignore")
    replacements, relevant, summary = compute_replacements(original)
    if not relevant:
        return False, None
    if not replacements:
        return False, summary

    patched = apply_replacements(original, replacements)
    if patched != original:
        path.write_text(patched, encoding="utf-8")
        return True, summary
    return False, summary


def main() -> int:
    patched_files: list[tuple[str, ReplacementSummary]] = []
    inspected_files: list[tuple[str, ReplacementSummary]] = []
    total_summary = ReplacementSummary()
    failures: list[str] = []

    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for file_path in iter_candidate_files(root):
            try:
                changed, summary = patch_file(file_path)
                if summary is None:
                    continue
                if changed:
                    patched_files.append((str(file_path), summary))
                    total_summary.uri_replacements += summary.uri_replacements
                    total_summary.provider_guards += summary.provider_guards
                    total_summary.markers += summary.markers
                else:
                    inspected_files.append((str(file_path), summary))
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{file_path}: {exc}")

    if patched_files:
        print("Patched run_in_terminal in:")
        for path, summary in patched_files:
            print(
                f" - {path} (uri replacements: {summary.uri_replacements}, "
                f"provider guards: {summary.provider_guards}, markers: {summary.markers})"
            )
        print(
            "Totals: files patched: "
            f"{len(patched_files)}, uri replacements: {total_summary.uri_replacements}, "
            f"provider guards: {total_summary.provider_guards}, markers: {total_summary.markers}"
        )
    if inspected_files and not patched_files:
        print("Found run_in_terminal without applicable replacements in:")
        for path, summary in inspected_files:
            print(
                f" - {path} (uri replacements: {summary.uri_replacements}, "
                f"provider guards: {summary.provider_guards}, markers: {summary.markers})"
            )
    if failures:
        print("Encountered errors while patching:")
        for failure in failures:
            print(f" - {failure}")

    if total_summary.markers == 0:
        print("Failed to patch run_in_terminal: no markers were inserted.")
        return 1

    return 0 if patched_files else 1


if __name__ == "__main__":
    raise SystemExit(main())
