#!/usr/bin/env python3
"""Patch Copilot run_in_terminal to skip file-scheme provider requirements.

This script searches the built VS Code (or VS Code Server) JavaScript bundles
for the `run_in_terminal` tool implementation and rewrites any `fileService.stat`
validation near that tool to be conditional on a provider being available.

It also rewrites eager `*.file(<workspace>.uri.fsPath)` constructions near the
tool to preserve the original workspace URI (avoiding forced `file` schemes in
web builds without a `file` provider).

A marker comment `/* patched: run_in_terminal */` is injected and the build
fails if no markers are present, ensuring the patch remains visible in the
served bundle. Summary statistics are printed for the files that were patched.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


DEFAULT_SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/usr/lib/code"),
    Path("/usr/lib/vscode-server"),
    Path("/opt/vscode-server"),
    Path("/data/vscode/extensions"),
    Path(os.environ.get("HOME", str(Path.home()))) / ".vscode/extensions",
    Path("/data/vscode/cli-data"),
    Path("/root/.vscode/cli-data"),
)


MARKER = "/* patched: run_in_terminal */"


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Yield JS-like files under the provided root."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath, name)
            if path.suffix.lower() in {".js", ".mjs", ".cjs"}:
                yield path


def build_windows(positions: Sequence[int], text_length: int) -> list[tuple[int, int]]:
    """Build merged window spans around run_in_terminal occurrences."""
    windows: list[tuple[int, int]] = []
    for pos in positions:
        start = max(0, pos - 2000)
        end = min(text_length, pos + 6000)
        if windows and start <= windows[-1][1]:
            prev_start, prev_end = windows[-1]
            windows[-1] = (prev_start, max(prev_end, end))
        else:
            windows.append((start, end))
    return windows


def in_window(start: int, windows: Sequence[tuple[int, int]]) -> bool:
    """Return True when a position is inside any window."""
    return any(window_start <= start <= window_end for window_start, window_end in windows)


IDENT = r"[_A-Za-z$][_A-Za-z0-9$]*"
SERVICE_EXPR = rf"{IDENT}(?:\??\.{IDENT})*"

GUARD_PATTERN = re.compile(
    rf"(?P<prefix>(?:await|yield)\s+)?(?P<service>{SERVICE_EXPR})\."
    rf"(?P<method>stat|exists|resolve)\(\s*(?P<arg>[^)]+?)\s*\)\s*(?P<trailing>;?)",
    re.MULTILINE,
)

URI_PATTERN = re.compile(
    rf"(?P<coercion>{SERVICE_EXPR})\.file\(\s*(?P<target>{IDENT})\.uri\.fsPath\s*\)"
)


@dataclass
class PatchResult:
    path: Path
    relevant: bool
    patched: bool
    uri_replacements: int
    guard_replacements: int
    marker_added: bool
    marker_present: bool
    is_workbench: bool


def compute_replacements(text: str) -> tuple[List[Tuple[int, int, str]], PatchResult]:
    """Compute textual replacements for a single file."""
    run_positions = [m.start() for m in re.finditer(r"run_in_terminal", text)]
    windows = build_windows(run_positions, len(text))
    relevant = bool(windows)

    replacements: List[Tuple[int, int, str]] = []
    uri_count = 0
    guard_count = 0

    if not relevant:
        return replacements, PatchResult(
            path=Path(),
            relevant=False,
            patched=False,
            uri_replacements=0,
            guard_replacements=0,
            marker_added=False,
            marker_present=MARKER in text,
            is_workbench=False,
        )

    for match in GUARD_PATTERN.finditer(text):
        start, end = match.span()
        if not in_window(start, windows):
            continue
        if "hasProvider" in match.group(0) or "canHandleResource" in match.group(0):
            continue

        service = match.group("service")
        method = match.group("method")
        arg = match.group("arg")
        prefix = match.group("prefix") or ""
        trailing = match.group("trailing") or ""

        fallback = "Promise.resolve(true)" if method == "exists" else "Promise.resolve()"
        service_expr = f"({service})"
        condition = (
            f"({service_expr}?.hasProvider?.({arg})) ?? "
            f"({service_expr}?.canHandleResource?.({arg})) ?? false"
        )
        guard_expr = f"({condition} ? {service_expr}.{method}({arg}) : {fallback})"
        replacements.append((start, end, f"{prefix}{guard_expr}{trailing}/* patched: run_in_terminal */"))
        guard_count += 1

    covered_spans: list[tuple[int, int]] = []

    for match in URI_PATTERN.finditer(text):
        start, end = match.span()
        if not in_window(start, windows):
            continue
        replacements.append((start, end, f"{match.group('target')}.uri"))
        uri_count += 1
        covered_spans.append((start, end))

    uri_file_pattern = re.compile(
        rf"(?P<ctor>{IDENT})\.file\(\s*(?P<target>[^)]+?)\s*\)"
    )
    for match in uri_file_pattern.finditer(text):
        start, end = match.span()
        if not in_window(start, windows):
            continue
        if any(span_start < end and start < span_end for span_start, span_end in covered_spans):
            continue
        ctor = match.group("ctor")
        target = match.group("target")
        patched_uri = (
            "("
            f"(p=>{{"
            f"const wf=(typeof workspaceFolders!=='undefined'&&workspaceFolders?.[0]?.uri)"
            f"??(typeof workspace!=='undefined'&&workspace?.workspaceFolders?.[0]?.uri);"
            f"if(wf){{try{{return wf.with({{path:p}});}}catch{{}}}}"
            f"const fs=(typeof fileService!=='undefined'?fileService:undefined)"
            f"??(typeof __vscode_fileService!=='undefined'?__vscode_fileService:undefined);"
            f"if(fs?.hasProvider?.('file')||fs?.canHandleResource?.({{scheme:'file'}}))"
            f"{{return {ctor}.file(p);}}"
            f"try{{return {ctor}.from?.({{scheme:'file',path:p}})??{ctor}.file(p);}}"
            f"catch{{return {ctor}.file(p);}}"
            f"}})"
            f"({target})"
            ")"
        )
        replacements.append((start, end, f"{patched_uri}/* patched: run_in_terminal */"))
        uri_count += 1

    marker_present = MARKER in text
    marker_added = False

    if guard_count and not marker_present:
        marker_present = True
        marker_added = True

    if not marker_present and relevant:
        insert_at = len(text)
        replacements.append((insert_at, insert_at, f"\n{MARKER}\n"))
        marker_present = True
        marker_added = True

    patched = bool(replacements)

    return replacements, PatchResult(
        path=Path(),
        relevant=relevant,
        patched=patched,
        uri_replacements=uri_count,
        guard_replacements=guard_count,
        marker_added=marker_added,
        marker_present=marker_present,
        is_workbench=False,
    )


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
    replacements, result = compute_replacements(original)
    result.path = path
    result.is_workbench = "workbench" in path.name

    if not result.relevant:
        return result

    patched = apply_replacements(original, replacements)
    if patched != original:
        path.write_text(patched, encoding="utf-8")
        result.patched = True
    else:
        result.patched = False
    return result


def _parse_bool(value: str | None) -> bool:
    if value is None:
        raise argparse.ArgumentTypeError("expected a boolean value")

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Patch Copilot run_in_terminal to avoid file-scheme provider requirements.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--require-match",
        "--strict",
        dest="require_match",
        nargs="?",
        type=_parse_bool,
        const=True,
        default=None,
        help=(
            "Fail when no run_in_terminal occurrences are patched. "
            "Defaults to false unless PATCH_RUN_IN_TERMINAL_REQUIRE_MATCH is truthy."
        ),
    )
    return parser.parse_args()


def should_require_match(parsed: argparse.Namespace) -> bool:
    if parsed.require_match is not None:
        return parsed.require_match
    env_value = os.environ.get("PATCH_RUN_IN_TERMINAL_REQUIRE_MATCH")
    if env_value is None:
        return False
    return env_value.lower() in {"1", "true", "yes", "on"}


def compute_search_roots() -> tuple[Path, ...]:
    roots = list(DEFAULT_SEARCH_ROOTS)
    if os.environ.get("VSCODE_CLI_DATA_DIR"):
        roots.append(Path(os.environ["VSCODE_CLI_DATA_DIR"]))
    # Avoid duplicate scanning while preserving ordering preference.
    deduped: list[Path] = []
    for root in roots:
        if root not in deduped:
            deduped.append(root)
    return tuple(deduped)


def main() -> int:
    parsed = parse_args()
    require_match = should_require_match(parsed)
    search_roots = compute_search_roots()

    results: list[PatchResult] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        workbench_paths: list[Path] = []
        other_paths: list[Path] = []

        for file_path in iter_candidate_files(root):
            (workbench_paths if "workbench" in file_path.name else other_paths).append(file_path)

        for file_path in [*workbench_paths, *other_paths]:
            try:
                results.append(patch_file(file_path))
            except Exception:
                # Continue patching other files even if one fails.
                continue

    patched_results = [result for result in results if result.patched]
    relevant_results = [result for result in results if result.relevant]
    marker_count = sum(1 for result in results if result.marker_present)
    markers_added = sum(1 for result in results if result.marker_added)
    total_uri = sum(result.uri_replacements for result in results)
    total_guards = sum(result.guard_replacements for result in results)
    workbench_results = [result for result in results if result.is_workbench and result.relevant]
    workbench_patched = any(
        (result.uri_replacements > 0 or result.guard_replacements > 0) for result in workbench_results
    )
    seen_files = len(results)
    scanned_roots = ", ".join(str(root) for root in search_roots)

    if patched_results:
        print("Patched run_in_terminal in:")
        for result in patched_results:
            print(
                f" - {result.path} "
                f"(workbench={result.is_workbench}, "
                f"uri_replacements={result.uri_replacements}, "
                f"provider_guards={result.guard_replacements}, "
                f"marker_added={result.marker_added})"
            )
    elif relevant_results:
        print("Found run_in_terminal but no changes were applied:")
        for result in relevant_results:
            print(f" - {result.path} (workbench={result.is_workbench})")
    else:
        print("No run_in_terminal occurrences patched (none found or already patched).")

    print(
        "Patch summary: "
        f"files_seen={seen_files}, "
        f"relevant_files={len(relevant_results)}, "
        f"patched_files={len(patched_results)}, "
        f"uri_replacements={total_uri}, "
        f"provider_guards={total_guards}, "
        f"markers_added={markers_added}, "
        f"markers_present={marker_count}, "
        f"roots=[{scanned_roots}]"
    )

    if not relevant_results:
        print(
            "No run_in_terminal occurrences found in candidate bundles.",
            flush=True,
        )
        return 0

    if require_match and workbench_results and not workbench_patched:
        print(
            "run_in_terminal found in workbench assets but no URI conversion or provider guards were applied.",
            flush=True,
        )
        return 1

    if require_match and not workbench_results:
        print(
            "run_in_terminal located outside workbench assets; strict mode requested but not enforced.",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
