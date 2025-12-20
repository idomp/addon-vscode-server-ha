#!/usr/bin/env python3
"""Patch Copilot run_in_terminal to skip file-scheme provider requirements.

This script searches the built VS Code (or VS Code Server) JavaScript bundles
for the `run_in_terminal` tool implementation and rewrites any `fileService.stat`
validation near that tool to be conditional on a provider being available.

It also rewrites eager `<something>.file(<workspace>.uri.fsPath)` constructions
near the tool to preserve the original workspace URI (avoiding forced `file`
schemes in web builds without a `file` provider). To ensure the patch is active,
the script appends a marker comment to patched bundles and fails if no bundle was
patched.
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
    Path("/home"),
    Path("/root"),
)

MIN_BUNDLE_SIZE = 1_000_000
MARKER = "/* patched: run_in_terminal */"
WINDOW_BEFORE = 800
WINDOW_AFTER = 4000

WORKBENCH_NAME = re.compile(r"workbench.*\.js$", re.IGNORECASE)


@dataclass
class PatchResult:
    path: Path
    changed: bool
    marker_present: bool
    guard_replacements: int
    uri_replacements: int


def iter_candidate_files() -> Iterable[Tuple[Path, str]]:
    """Yield candidate bundle paths and their text content."""
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if not WORKBENCH_NAME.search(name):
                    continue
                path = Path(dirpath, name)
                try:
                    if path.stat().st_size < MIN_BUNDLE_SIZE:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if "run_in_terminal" in text:
                    yield path, text


def compute_replacements(text: str) -> Tuple[List[Tuple[int, int, str]], bool, int, int]:
    """Compute textual replacements for a single file.

    Returns a tuple containing:
    - A list of (start, end, replacement) tuples.
    - A bool indicating whether the file is relevant (contains run_in_terminal).
    - Number of provider guard replacements.
    - Number of URI conversions replaced.
    """
    run_positions = [m.start() for m in re.finditer(r"run_in_terminal", text)]
    if not run_positions:
        return [], False, 0, 0

    replacements: List[Tuple[int, int, str]] = []
    guard_replacements = 0
    uri_replacements = 0

    # Guard fileService stat/exists/resolve usage near the tool registration/handler.
    guarded_pattern = re.compile(
        r"await\s+((?:[A-Za-z0-9_$]+\.)*[A-Za-z0-9_$]+)\.(stat|exists|resolve)\(([^)]+)\)"
    )
    for match in guarded_pattern.finditer(text):
        start, end = match.span()
        if any(pos - WINDOW_BEFORE <= start <= pos + WINDOW_AFTER for pos in run_positions):
            service, method, arg = match.groups()
            fallback = "Promise.resolve(true)" if method == "exists" else "Promise.resolve()"
            replacement = (
                f"await ((({service}.hasProvider?.({arg})) ?? "
                f"({service}.canHandleResource?.({arg})) ?? false) "
                f"? {service}.{method}({arg}) : {fallback});"
            )
            guard_replacements += 1
            replacements.append((start, end, replacement))

    # Avoid forced file-scheme URIs near the tool.
    uri_pattern = re.compile(
        r"([A-Za-z0-9_$]+)\.file\(\s*([A-Za-z0-9_$]+)\.uri\.fsPath\s*\)"
    )
    for match in uri_pattern.finditer(text):
        start, end = match.span()
        if any(pos - WINDOW_BEFORE <= start <= pos + WINDOW_AFTER for pos in run_positions):
            workspace_var = match.group(2)
            uri_replacements += 1
            replacements.append((start, end, f"{workspace_var}.uri /* patched: keep scheme */"))

    return replacements, True, guard_replacements, uri_replacements


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    # Apply replacements from the end to preserve offsets.
    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def patch_file(path: Path, original: str) -> PatchResult:
    replacements, relevant, guard_replacements, uri_replacements = compute_replacements(original)
    if not relevant:
        return PatchResult(path, False, False, 0, 0)

    if not replacements:
        marker_present = MARKER in original
        return PatchResult(path, False, marker_present, 0, 0)

    patched = apply_replacements(original, replacements)
    marker_present = MARKER in patched
    if not marker_present:
        patched = f"{patched}\n{MARKER}\n"
        marker_present = True

    changed = patched != original
    if changed:
        path.write_text(patched, encoding="utf-8")
    return PatchResult(path, changed, marker_present, guard_replacements, uri_replacements)


def main() -> int:
    results: list[PatchResult] = []
    for path, content in iter_candidate_files():
        try:
            results.append(patch_file(path, content))
        except Exception:
            # Continue patching other files even if one fails.
            continue

    patched_with_marker = [r for r in results if r.marker_present]

    if results:
        print("run_in_terminal patch results:")
        for result in results:
            status = "updated" if result.changed else "unchanged"
            print(
                f" - {result.path}: uri replacements={result.uri_replacements}, "
                f"provider guards={result.guard_replacements}, marker={'yes' if result.marker_present else 'no'} "
                f"({status})"
            )
    else:
        print("No run_in_terminal occurrences found in candidate bundles.")

    if not patched_with_marker:
        print("ERROR: No bundles patched with marker comment; build should fail.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
