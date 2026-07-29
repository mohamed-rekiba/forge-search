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

cleanup() { rm -rf "$VENV"; }
trap cleanup EXIT

# Every failure below says what it saw. This script's assertions exist to guard
# diagnostics, and in CI nobody can open $STDERR after the run — so a bare exit
# code would leave the reader worse off than the bug being caught.
fail() {
    echo "smoke FAILED: $1" >&2
    shift
    for line in "$@"; do echo "  $line" >&2; done
    exit 1
}

rm -rf "$VENV"
uv venv "$VENV" --quiet

# Unquoted glob on purpose: `make build` clears dist first, so this resolves to
# exactly one wheel. If it ever resolves to two, uv fails loudly rather than
# installing whichever one sorted first — which is how this gate spent two
# releases testing 0.4.2.
# shellcheck disable=SC2086
VIRTUAL_ENV="$VENV" uv pip install --quiet dist/*.whl

"$VENV/bin/forge-search" --help > /dev/null

mkdir -p "$CORPUS"
echo "validate_token" > "$CORPUS/a.md"
if ! "$VENV/bin/forge-search" search validate_token --root "$CORPUS" | grep -q a.md; then
    fail "the wheel installed and ran, but a search over a one-file corpus" \
         "returned no hit. The packaged CLI is not finding content."
fi

# Nothing is discovered, by design. A missing --root has to name the fix rather
# than quietly indexing the working directory.
if "$VENV/bin/forge-search" serve 2>"$STDERR"; then
    fail "serve exited 0 with no --root. It must refuse rather than guess a" \
         "root. Its stderr was:" "$(cat "$STDERR")"
fi
if ! grep -q -- "--root" "$STDERR"; then
    fail "serve refused a missing --root but never mentioned --root, so the" \
         "message does not tell the reader what to fix:" "$(cat "$STDERR")"
fi

echo "smoke: wheel installs, CLI runs, search hits, missing --root fails loudly"
