#!/usr/bin/env python3
"""Patch Copilot run_in_terminal to skip file-scheme provider requirements.

This script searches the built VS Code (or VS Code Server) JavaScript bundles
for the `run_in_terminal` tool implementation and rewrites any `fileService.stat`
validation near that tool to be conditional on a provider being available.

It also rewrites eager `URI.file(<workspace>.uri.fsPath)` constructions near the
tool to preserve the original workspace URI (avoiding forced `file` schemes in
web builds without a `file` provider).

The patcher reports how many replacements were made and ensures a marker comment
is present so the build fails loudly if the served bundle was not updated.
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
WINDOW_BEFORE = 1200
WINDOW_AFTER = 5000
WORKBENCH_NAME_RE = re.compile(r"workbench.*\.(?:m?js)$", re.IGNORECASE)
MARKER = "/* patched: run_in_terminal */"
MARKER_PATTERN = re.compile(r"/\*\s*patched:\s*run_in_terminal\s*\*/")
PROVIDER_PATTERN = re.compile(
    r"await\s+"
    r"(?P<service>(?:[A-Za-z0-9_$]+\.)*[A-Za-z0-9_$]+)\."
    r"(?P<method>stat|exists|resolve)"
    r"\(\s*(?P<arg>[^)]*?)\s*\)\s*;?",
    re.DOTALL,
)
URI_PATTERN = re.compile(
    r"(?P<callee>[A-Za-z0-9_$]+)\.file\(\s*(?P<workspace>[A-Za-z0-9_$]+)\.uri\.fsPath\s*\)",
    re.DOTALL,
)
RUN_TOKEN_PATTERN = re.compile(r"run_in_terminal")


@dataclass
class PatchStats:
    path: Path
    is_workbench: bool
    uri_replacements: int
    provider_guards: int
    marker_added: int
    marker_total: int
    patched: bool
    relevant: bool


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Yield JS-like files under the provided root."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath, name)
            if path.suffix.lower() in {".js", ".mjs", ".cjs"}:
                yield path


def merge_windows(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not ranges:
        return []
    ranges.sort()
    merged: List[Tuple[int, int]] = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def compute_replacements(text: str) -> Tuple[List[Tuple[int, int, str]], PatchStats]:
    """Compute textual replacements for a single file."""
    run_positions = [m.start() for m in RUN_TOKEN_PATTERN.finditer(text)]
    if not run_positions:
        return [], PatchStats(
            path=Path(),
            is_workbench=False,
            uri_replacements=0,
            provider_guards=0,
            marker_added=0,
            marker_total=len(MARKER_PATTERN.findall(text)),
            patched=False,
            relevant=False,
        )

    windows = merge_windows(
        [
            (
                max(0, position - WINDOW_BEFORE),
                min(len(text), position + WINDOW_AFTER),
            )
            for position in run_positions
        ]
    )

    replacements: List[Tuple[int, int, str]] = []
    uri_replacements = 0
    provider_guards = 0
    seen_spans: set[Tuple[int, int]] = set()

    for window_start, window_end in windows:
        for match in PROVIDER_PATTERN.finditer(text, window_start, window_end):
            start, end = match.span()
            if (start, end) in seen_spans:
                continue
            seen_spans.add((start, end))
            service = match.group("service")
            method = match.group("method")
            arg = match.group("arg")
            fallback = "Promise.resolve(true)" if method == "exists" else "Promise.resolve()"
            replacement = (
                f"await ((({service}.hasProvider?.({arg})) ?? "
                f"({service}.canHandleResource?.({arg})) ?? false) "
                f"? {service}.{method}({arg}) : {fallback});"
            )
            replacements.append((start, end, replacement))
            provider_guards += 1

        for match in URI_PATTERN.finditer(text, window_start, window_end):
            start, end = match.span()
            if (start, end) in seen_spans:
                continue
            seen_spans.add((start, end))
            workspace_var = match.group("workspace")
            replacements.append((start, end, f"{workspace_var}.uri"))
            uri_replacements += 1

    marker_total = len(MARKER_PATTERN.findall(text))
    marker_added = 0
    if marker_total == 0:
        replacements.append((0, 0, f"{MARKER}\n"))
        marker_added = 1
        marker_total = 1

    patched_stats = PatchStats(
        path=Path(),
        is_workbench=False,
        uri_replacements=uri_replacements,
        provider_guards=provider_guards,
        marker_added=marker_added,
        marker_total=marker_total,
        patched=False,
        relevant=True,
    )
    return replacements, patched_stats


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    # Apply replacements from the end to preserve offsets.
    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def patch_file(path: Path) -> PatchStats:
    original = path.read_text(encoding="utf-8", errors="ignore")
    replacements, stats = compute_replacements(original)
    if not stats.relevant:
        return stats

    patched = apply_replacements(original, replacements)
    if patched != original:
        path.write_text(patched, encoding="utf-8")
        stats.patched = True
    stats.path = path
    stats.is_workbench = bool(WORKBENCH_NAME_RE.search(path.name))
    return stats


def main() -> int:
    patched_stats: list[PatchStats] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for file_path in iter_candidate_files(root):
            try:
                stats = patch_file(file_path)
            except Exception:
                # Continue patching other files even if one fails.
                continue
            if stats.relevant:
                patched_stats.append(stats)

    if not patched_stats:
        print("No run_in_terminal occurrences found in search roots.")
        return 1

    total_uri = sum(stat.uri_replacements for stat in patched_stats)
    total_providers = sum(stat.provider_guards for stat in patched_stats)
    total_markers = sum(stat.marker_total for stat in patched_stats)
    workbench_hits = [stat for stat in patched_stats if stat.is_workbench]
    workbench_markers_present = any(
        stat.is_workbench and stat.marker_total > 0 for stat in patched_stats
    )

    print("run_in_terminal patch results:")
    for stat in patched_stats:
        status = "patched" if stat.patched else "unchanged"
        scope = "workbench bundle" if stat.is_workbench else "other bundle"
        print(
            f" - {stat.path}: {status} ({scope}); "
            f"URI replacements: {stat.uri_replacements}, "
            f"provider guards: {stat.provider_guards}, "
            f"markers added: {stat.marker_added}, "
            f"markers total: {stat.marker_total}"
        )

    if not workbench_hits:
        print("No workbench bundles containing run_in_terminal were found.")
        return 1

    if total_markers == 0:
        print("No marker comments were inserted; failing the build.")
        return 1

    if not workbench_markers_present:
        print("No run_in_terminal markers were written to workbench bundles.")
        return 1

    print(
        "Summary: "
        f"{len(patched_stats)} relevant file(s), "
        f"{total_uri} URI replacement(s), "
        f"{total_providers} provider guard(s), "
        f"{total_markers} marker(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
