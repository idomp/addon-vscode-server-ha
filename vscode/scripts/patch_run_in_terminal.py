#!/usr/bin/env python3
"""Patch Copilot run_in_terminal to skip file-scheme provider requirements.

This script searches the built VS Code (or VS Code Server) JavaScript bundles
for the `run_in_terminal` tool implementation and rewrites any `fileService.stat`
validation near that tool to be conditional on a provider being available.

It also rewrites eager `URI.file(<workspace>.uri.fsPath)` constructions near the
tool to preserve the original workspace URI (avoiding forced `file` schemes in
web builds without a `file` provider).

Each patched bundle is marked with ``/* patched: run_in_terminal */`` to make
verification easy in build/start logs and to allow runtime greps against the
served workbench bundle. Verification fails if a relevant bundle does not carry
the marker.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/usr/lib/code"),
    Path("/usr/lib/vscode-server"),
    Path("/opt/vscode-server"),
    Path("/usr/share/code"),
)

PATCH_MARKER = "/* patched: run_in_terminal */"


@dataclass
class PatchResult:
    path: Path
    relevant: bool
    changed: bool
    marker_present: bool
    replacements_applied: int


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

    return replacements, True


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    # Apply replacements from the end to preserve offsets.
    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def ensure_marker(text: str) -> tuple[str, bool]:
    """Ensure the patch marker is present, returning (text, added?)."""
    if PATCH_MARKER in text:
        return text, False
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}{PATCH_MARKER}\n", True


def patch_file(path: Path) -> PatchResult:
    original = path.read_text(encoding="utf-8", errors="ignore")
    replacements, relevant = compute_replacements(original)
    patched_text = original
    replacements_applied = len(replacements)

    if replacements_applied:
        patched_text = apply_replacements(original, replacements)
        patched_text, _ = ensure_marker(patched_text)
        if patched_text != original:
            path.write_text(patched_text, encoding="utf-8")
    elif relevant and PATCH_MARKER in original:
        # Relevant bundle already contains the marker; leave as-is.
        patched_text = original

    return PatchResult(
        path=path,
        relevant=relevant,
        changed=relevant and (patched_text != original),
        marker_present=relevant and (PATCH_MARKER in patched_text),
        replacements_applied=replacements_applied,
    )


def print_results(results: Sequence[PatchResult], *, require_patch: bool) -> int:
    patched = [r for r in results if r.relevant and r.changed]
    marked = [r for r in results if r.relevant and r.marker_present]
    workbench_like = [r for r in marked if "workbench" in str(r.path).lower()]

    if patched:
        print("Patched run_in_terminal provider checks in:")
        for result in patched:
            print(
                f" - {result.path} "
                f"(replacements: {result.replacements_applied}, marker: {result.marker_present})"
            )
    elif marked:
        print("run_in_terminal already patched; marker present in:")
        for result in marked:
            print(f" - {result.path}")
    else:
        print("No run_in_terminal occurrences patched (none found or already patched).")

    if marked:
        print("Verified patch markers in:")
        for result in marked:
            print(f" - {result.path}")

    if workbench_like:
        print("Workbench-like bundles patched/verified:")
        for result in workbench_like:
            print(f" - {result.path}")

    if require_patch and not marked:
        print("ERROR: run_in_terminal patch not found; marker missing.")
        return 1

    missing_marker = [r for r in results if r.relevant and not r.marker_present]
    if missing_marker:
        print("ERROR: marker missing from relevant bundles:")
        for result in missing_marker:
            print(f" - {result.path}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch GitHub Copilot run_in_terminal for VS Code web/server builds."
    )
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        dest="roots",
        help="Additional search root for bundles (can be passed multiple times).",
    )
    parser.add_argument(
        "--require-patch",
        action="store_true",
        help="Exit non-zero if no run_in_terminal bundle is patched/marked.",
    )
    args = parser.parse_args()

    search_roots: tuple[Path, ...] = tuple(dict.fromkeys((args.roots or []) + list(SEARCH_ROOTS)))

    results: list[PatchResult] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        for file_path in iter_candidate_files(root):
            try:
                results.append(patch_file(file_path))
            except Exception:  # pragma: no cover - best-effort patching
                # Continue patching other files even if one fails.
                continue

    print("Search roots:")
    for root in search_roots:
        print(f" - {root}")

    return print_results(results, require_patch=args.require_patch)


if __name__ == "__main__":
    raise SystemExit(main())
