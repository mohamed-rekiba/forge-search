#!/usr/bin/env bash
#
# Re-lock the open release PR, so uv.lock ships with the release.
#
# release-please bumps the version in pyproject.toml and knows nothing about
# uv.lock, so the lock keeps naming the previous version. The first `uv sync` in
# a fresh clone then rewrites it, and every contributor starts with a diff they
# did not make. A lockfile that disagrees with the released tree is also the one
# thing a lockfile exists to prevent.
#
# Runs from release-please.yml, in the same job that opened or updated the PR.
# It cannot be its own workflow: release-please creates that PR with the default
# GITHUB_TOKEN, and PRs created by that token do not trigger `pull_request`
# workflows — so an event-driven re-lock would never fire.
#
# For the same reason, the commit this pushes does not re-run the PR's checks.
# That is acceptable for a lockfile-only change, and it is why the commit says
# what it is rather than hiding in the release commit.
set -euo pipefail

fail() {
    echo "relock FAILED: $1" >&2
    shift
    for chunk in "$@"; do
        [ -n "$chunk" ] || continue
        printf '%s\n' "$chunk" | while IFS= read -r line; do echo "  $line" >&2; done
    done
    exit 1
}

# Find the release PR by its configured title pattern rather than by guessing
# the branch name. `pull-request-title-pattern` in release-please-config.json is
# `chore: release ${version}`, and the branch name convention
# (release-please--branches--…) is release-please's internal detail, not a
# contract. The action's own `pr` output is a JSON object whose branch field is
# undocumented, so it is not a safer bet either.
PR_JSON=$(gh pr list --state open --json number,headRefName,title --limit 20)

BRANCH=$(printf '%s' "$PR_JSON" | jq -r '
    [.[] | select(.title | startswith("chore: release "))] | first | .headRefName // empty
')
NUMBER=$(printf '%s' "$PR_JSON" | jq -r '
    [.[] | select(.title | startswith("chore: release "))] | first | .number // empty
')

if [ -z "$BRANCH" ]; then
    # Not an error: release-please updates an existing PR on most pushes, and
    # opens none at all when there is nothing releasable.
    echo "relock: no open release PR, nothing to do"
    exit 0
fi

echo "relock: found release PR #$NUMBER on $BRANCH"

git fetch origin "$BRANCH" --quiet
git checkout "$BRANCH" --quiet

uv lock --quiet

if git diff --quiet -- uv.lock; then
    echo "relock: uv.lock already matches the bumped version, nothing to push"
    exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add uv.lock
git commit --quiet -m "chore: re-lock for the release

release-please bumps pyproject.toml but not uv.lock, so this keeps the
lockfile agreeing with the version being released."

git push origin "$BRANCH" --quiet || fail \
    "could not push the re-lock to $BRANCH." \
    "The release PR will merge with a stale uv.lock unless this is fixed." \
    "Check that the workflow grants contents: write."

echo "relock: pushed a re-lock to $BRANCH"
