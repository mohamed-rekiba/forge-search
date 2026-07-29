#!/usr/bin/env bash
#
# Install the built wheel into a clean venv and actually run it.
#
# Catches packaging faults no test can see: a module missing from the wheel, a
# broken entry point, an import that only resolves from source. `serve` has
# shipped a startup crash once already.
#
# Run via `make smoke`, which builds first. CI runs the same target rather than
# its own copy — the two diverged once, and the broken one was the one a
# contributor runs by hand.
set -euo pipefail

VENV=.smoke
CORPUS="$VENV/corpus"
STDERR="$VENV/err.txt"
STDOUT="$VENV/out.txt"

# Keep the venv when something failed, so it can be inspected. Removing it on
# every path — as an unconditional trap does — takes away the evidence at the
# same moment it becomes useful.
cleanup() {
    status=$?
    if [ "$status" -eq 0 ]; then
        rm -rf "$VENV"
    elif [ -d "$VENV" ]; then
        # Guarded: the trap is installed before the venv exists, so an early
        # failure would otherwise announce a directory that was never created.
        echo "smoke: leaving $VENV in place for inspection" >&2
    fi
    exit "$status"
}
trap cleanup EXIT

# Every assertion below says what it saw when it fires. They exist to guard
# diagnostics, and in CI nobody can open these files after the run — so a bare
# exit code would leave the reader worse off than the bug being caught.
fail() {
    echo "smoke FAILED: $1" >&2
    shift
    for chunk in "$@"; do
        [ -n "$chunk" ] || continue
        # Indent every line, not just the first: stderr from the CLI is
        # multi-line and would otherwise lose the indent after line one.
        printf '%s\n' "$chunk" | while IFS= read -r line; do
            echo "  $line" >&2
        done
    done
    exit 1
}

[ -f pyproject.toml ] && [ -d dist ] || fail \
    "run this from the repository root, after a build — expected ./pyproject.toml" \
    "and ./dist here. Use \`make smoke\`, which builds first."

rm -rf "$VENV"
uv venv "$VENV" --quiet

# Unquoted glob on purpose: `make build` clears dist first, so this resolves to
# exactly one wheel. If it ever resolves to two, uv fails loudly rather than
# installing whichever one sorted first — which is how this gate spent two
# releases testing 0.4.2.
# shellcheck disable=SC2086
VIRTUAL_ENV="$VENV" uv pip install --quiet dist/*.whl

"$VENV/bin/forge-search" --help > /dev/null 2>"$STDERR" || fail \
    "the wheel installed but \`--help\` failed, so the entry point is broken." \
    "Its stderr was:" "$(cat "$STDERR")"

mkdir -p "$CORPUS"
echo "validate_token" > "$CORPUS/a.md"
# Capture, then grep the file. Piping straight into `grep -q` is unsafe under
# `pipefail`: grep exits at the first match, the producer takes SIGPIPE, and
# pipefail promotes that to the pipeline's status — failing an assertion whose
# search actually succeeded. A false alarm here is the same kind of harm as the
# silent failure this script exists to prevent.
#
# stdout and stderr go to separate files, and only stdout is grepped. Merging
# them lets a stderr line that happens to name a corpus file — `warning: skipped
# a.md` — satisfy the grep while stdout held no result at all. That failure is
# worse than the one above: it passes quietly instead of complaining.
"$VENV/bin/forge-search" search validate_token --root "$CORPUS" \
    >"$STDOUT" 2>"$STDERR"
if ! grep -q a.md "$STDOUT"; then
    fail "the wheel installed and ran, but a search over a one-file corpus" \
         "returned no hit, so the packaged CLI is not finding content." \
         "stdout was:" "$(cat "$STDOUT")" \
         "stderr was:" "$(cat "$STDERR")"
fi

# Nothing is discovered, by design. A missing --root has to name the fix rather
# than quietly indexing the working directory.
if "$VENV/bin/forge-search" serve 2>"$STDERR"; then
    fail "serve exited 0 with no --root. It must refuse rather than guess a root." \
         "Its stderr was:" "$(cat "$STDERR")"
fi
if ! grep -q -- "--root" "$STDERR"; then
    fail "serve refused a missing --root but never mentioned --root, so the" \
         "message does not tell the reader what to fix:" "$(cat "$STDERR")"
fi

echo "smoke: wheel installs, CLI runs, search hits, missing --root fails loudly"
