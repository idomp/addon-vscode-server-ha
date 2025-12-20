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

import argparse
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

PATCH_MARKER = "/* patched: run_in_terminal */"
DEFAULT_SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/usr/lib/code"),
    Path("/usr/lib/vscode-server"),
    Path("/opt/vscode-server"),
)
# How far around a run_in_terminal occurrence we search for related code paths.
WINDOW_BEFORE = 1500
WINDOW_AFTER = 6000


@dataclass
class PatchOutcome:
    """Capture the patch/verify result for a single bundle."""

    path: Path
    relevant: bool
    replacements_before: int
    replacements_after: int
    marker_present: bool
    changed: bool
    needs_manual: bool

    @property
    def status(self) -> str:
        if not self.relevant:
            return "irrelevant"
        if self.needs_manual:
            return "needs-manual-review"
        if self.replacements_after:
            return "needs-patch"
        if self.changed:
            return "patched"
        if self.marker_present:
            return "already-patched"
        return "unmarked"

    @property
    def is_patched(self) -> bool:
        return (
            self.relevant
            and not self.replacements_after
            and self.marker_present
            and not self.needs_manual
        )


def discover_search_roots(extra_roots: Sequence[Path] | None = None) -> List[Path]:
    """Return a deduplicated list of search roots."""
    roots: set[Path] = set(DEFAULT_SEARCH_ROOTS)
    if extra_roots:
        roots.update(extra_roots)

    code_path = shutil.which("code")
    if code_path:
        binary = Path(code_path).resolve()
        parents = list(binary.parents)[:4]  # Up to /usr
        parents.append(binary.parent)
        for ancestor in parents:
            if ancestor == Path("/"):
                continue
            if any(token in ancestor.name for token in ("code", "vscode")):
                roots.add(ancestor)
            for subdir in (
                "resources",
                "resources/app",
                "resources/server",
                "resources/server/out",
                "resources/server/web",
            ):
                candidate = ancestor / subdir
                if candidate.exists():
                    roots.add(candidate)

    return sorted(root for root in roots if root.exists())


def iter_candidate_files(roots: Sequence[Path]) -> Iterable[Path]:
    """Yield JS-like files under the provided roots."""
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                path = Path(dirpath, name)
                if path.suffix.lower() in {".js", ".mjs", ".cjs"} and path not in seen:
                    seen.add(path)
                    yield path


def find_run_positions(text: str) -> List[int]:
    """Locate run_in_terminal occurrences for proximity matching."""
    return [m.start() for m in re.finditer(r"run_in_terminal", text)]


def compute_replacements(text: str, run_positions: Sequence[int]) -> List[Tuple[int, int, str]]:
    """Compute textual replacements for a single file."""
    replacements: List[Tuple[int, int, str]] = []

    guarded_pattern = re.compile(
        r"await\s+((?:[A-Za-z0-9_$]+\.)*[A-Za-z0-9_$]+)\.(stat|exists|resolve)\(([^)]+)\)\s*;?"
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
            replacements.append((start, end, replacement))

    uri_pattern = re.compile(
        r"URI\.file\(\s*([A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*)\.uri\.fsPath\s*\)"
    )
    for match in uri_pattern.finditer(text):
        start, end = match.span()
        if any(pos - WINDOW_BEFORE <= start <= pos + WINDOW_AFTER for pos in run_positions):
            workspace_var = match.group(1)
            replacements.append((start, end, f"{workspace_var}.uri"))

    return replacements


def has_file_service_usage(text: str, run_positions: Sequence[int]) -> bool:
    """Check whether fileService calls exist near run_in_terminal."""
    file_service_pattern = re.compile(r"fileService\.[A-Za-z]+")
    for match in file_service_pattern.finditer(text):
        if any(pos - WINDOW_BEFORE <= match.start() <= pos + WINDOW_AFTER for pos in run_positions):
            return True
    return False


def apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
    if not replacements:
        return text

    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, replacement in replacements:
        text = text[:start] + replacement + text[end:]
    return text


def patch_file(path: Path, *, write_changes: bool) -> PatchOutcome:
    original = path.read_text(encoding="utf-8", errors="ignore")
    run_positions = find_run_positions(original)
    if not run_positions:
        return PatchOutcome(path, False, 0, 0, PATCH_MARKER in original, False, False)

    replacements = compute_replacements(original, run_positions)
    needs_manual = has_file_service_usage(original, run_positions) and not replacements
    updated = original
    changed = False

    if replacements and write_changes:
        updated = apply_replacements(updated, replacements)
        changed = updated != original

    if write_changes and (replacements or not needs_manual):
        if PATCH_MARKER not in updated:
            updated = f"{PATCH_MARKER}\n{updated}"
            changed = True

    if changed and write_changes:
        path.write_text(updated, encoding="utf-8")

    post_text = updated if write_changes else original
    post_positions = find_run_positions(post_text)
    post_replacements = compute_replacements(post_text, post_positions)
    marker_present = PATCH_MARKER in post_text

    return PatchOutcome(
        path=path,
        relevant=True,
        replacements_before=len(replacements),
        replacements_after=len(post_replacements),
        marker_present=marker_present,
        changed=changed if write_changes else False,
        needs_manual=needs_manual,
    )


def summarize(results: List[PatchOutcome]) -> int:
    if not results:
        print("ERROR: No run_in_terminal bundles were located.")
        return 1

    patched = [r for r in results if r.status == "patched"]
    already = [r for r in results if r.status == "already-patched"]
    unmarked = [r for r in results if r.relevant and not r.marker_present]
    pending = [r for r in results if r.replacements_after or r.needs_manual]

    print("run_in_terminal patch summary:")
    for outcome in results:
        print(
            f" - {outcome.path} [{outcome.status}; "
            f"replacements {outcome.replacements_before}->{outcome.replacements_after}; "
            f"marker={'yes' if outcome.marker_present else 'no'}]"
        )

    if pending or unmarked:
        print("ERROR: run_in_terminal bundles are missing the patch or marker:")
        for outcome in pending + unmarked:
            print(f"   - {outcome.path} ({outcome.status})")
        return 1

    print(
        f"Patched run_in_terminal bundles: {len(patched)} updated, "
        f"{len(already)} already marked."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        default=[],
        help="Additional search root (can be specified multiple times).",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Do not write changes; exit non-zero if patching would be required.",
    )
    parser.add_argument(
        "--list-bundles",
        action="store_true",
        help="Print run_in_terminal bundle paths that were scanned.",
    )

    args = parser.parse_args(argv)
    search_roots = discover_search_roots(args.root)

    results: List[PatchOutcome] = []
    for file_path in iter_candidate_files(search_roots):
        try:
            outcome = patch_file(file_path, write_changes=not args.verify_only)
        except Exception as exc:  # pragma: no cover - best effort patching
            print(f"Skipping {file_path} due to error: {exc}")
            continue
        if outcome.relevant:
            results.append(outcome)

    if args.list_bundles:
        print("run_in_terminal bundle candidates:")
        for outcome in results:
            if outcome.relevant:
                print(f" - {outcome.path}")

    return summarize(results)


if __name__ == "__main__":
    raise SystemExit(main())
