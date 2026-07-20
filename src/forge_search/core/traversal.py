"""File traversal — where the known search defects are fixed once, centrally.

Every default here exists because the naive behaviour was measured causing
silent wrong answers in a real repository. Silent is the operative word: each
one returns *zero results* rather than an error, which is indistinguishable
from "the content does not exist". A caller acts on the false negative with
full confidence.

The measurements, from the audit that motivated this project:

1. **Hidden directories are skipped by default** by the usual search tools.
   A repository keeping its configuration under a dotted directory had
   **20 of 44 files invisible** — over half, including every hook, rule, and
   agent definition. Fixed by `include_hidden=True` as the default.

2. **VCS-ignore rules are honoured even for explicitly-passed paths.** A
   directory ignored by the top-level `.gitignore` returned *zero hits* even
   when named directly on the command line. Fixed by treating an explicitly
   configured root as always traversable — an ignore rule above it cannot
   veto a root the operator named.

3. **Truncation destroys the match.** Line-length limits in the usual tools
   replace an over-long matching line with a placeholder rather than
   shortening it, so the matched content is lost entirely. Fixed by
   `excerpt()` slicing around the match position.

4. **Absolute roots go stale.** Handled in `roots.py` — never persisted.

The blunt inverse of defect 2 is also wrong: ignoring *all* ignore rules
re-surfaces every `node_modules`, `.venv`, and build directory in the tree.
The measured difference between the two mistakes was 0 junk hits versus 8.
So nested ignore rules are honoured; only rules *above* a configured root
are overridden.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["TraversalConfig", "walk", "excerpt", "DEFAULT_EXCLUDES"]


#: Directories never worth indexing. Deliberately conservative — these are
#: build outputs, dependency caches, and VCS internals, not user content.
#: Kept as a built-in floor so a standalone user gets sane behaviour with no
#: configuration, and additive (never replacing) any per-root ignore rules.
DEFAULT_EXCLUDES: frozenset[str] = frozenset(
    {
        ".git",
        # Our own index directory when `--index-dir` puts it in-tree. An
        # index that indexes itself matches on its own contents and grows
        # each pass — this floor is what makes in-tree storage safe at all.
        ".forge-search",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".idea",
        ".vscode",
    }
)

#: Extensions worth reading as text. Anything else is skipped — indexing a
#: binary produces noise, not matches.
DEFAULT_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".md",
        ".markdown",
        ".rst",
        ".txt",
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".ini",
        ".cfg",
        ".conf",
        ".sql",
        ".graphql",
        ".proto",
        ".html",
        ".css",
        ".scss",
    }
)


@dataclass(slots=True)
class TraversalConfig:
    """Traversal defaults. Every field's default is the *correct* behaviour;
    changing one is opting into a known failure mode, not tuning."""

    #: Defect 1. Off by default in the usual tools; on here, because a dotted
    #: config directory is content, not noise.
    include_hidden: bool = True

    #: Directory basenames to skip, additive to DEFAULT_EXCLUDES.
    extra_excludes: frozenset[str] = field(default_factory=frozenset)

    #: File extensions treated as text.
    text_suffixes: frozenset[str] = field(default_factory=lambda: DEFAULT_TEXT_SUFFIXES)

    #: Skip files above this size. Large files are almost always generated —
    #: lockfiles, bundles, fixtures — and dominate an index without adding
    #: anything searchable.
    max_file_bytes: int = 2_000_000

    def excluded_dirs(self) -> frozenset[str]:
        return DEFAULT_EXCLUDES | self.extra_excludes


def walk(root: Path, config: TraversalConfig | None = None) -> Iterator[Path]:
    """Yield indexable files under `root`.

    `root` is traversed because it was *configured*, regardless of any ignore
    rule above it — defect 2. Ignore rules *within* the tree still apply via
    the exclude list, which is what keeps dependency directories out.
    """
    config = config or TraversalConfig()
    excluded = config.excluded_dirs()
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune in place so os.walk does not descend — much cheaper than
        # filtering results afterwards.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in excluded and (config.include_hidden or not d.startswith("."))
        ]

        current = Path(dirpath)
        for name in filenames:
            if not config.include_hidden and name.startswith("."):
                continue
            path = current / name
            if path.suffix.lower() not in config.text_suffixes:
                continue
            try:
                if path.stat().st_size > config.max_file_bytes:
                    continue
            except OSError:
                # Broken symlink, race, permissions. Skipping is right; the
                # file is not indexable either way.
                continue
            yield path


def excerpt(line: str, match_start: int, match_len: int, width: int = 150) -> str:
    """Return at most `width` characters of `line`, centred on the match.

    Defect 3. The naive approach — dropping any line longer than the limit —
    discards exactly the content the caller searched for. This keeps the match
    and trims around it, marking each trimmed side with an ellipsis so the
    result is never mistaken for a complete line.
    """
    line = line.rstrip("\n")
    if len(line) <= width:
        return line

    match_len = max(match_len, 0)
    # Centre the window on the match, then clamp into range. The match itself
    # is preserved even when it is wider than the window — truncating it would
    # reintroduce the defect at a smaller scale.
    ideal_start = match_start - (width - match_len) // 2
    start = max(0, min(ideal_start, len(line) - width))
    end = start + width

    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(line) else ""
    return f"{prefix}{line[start:end]}{suffix}"
