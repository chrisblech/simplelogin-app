#!/usr/bin/env bash
#
# Dry run of an upstream upgrade: merges the DEPLOY_BRANCHES from
# sync-deploy.sh onto an upstream release in a throwaway git worktree and
# reports every problem at once. Changes no branch, pushes nothing.
#
# Usage:
#   scripts/upgrade-preflight.sh [--test] [--keep] [--local] [TAG]
#
#   TAG     upstream release to test against (default: latest upstream tag)
#   --test  additionally run alembic migrations + pytest against throwaway
#           postgres and redis containers (takes a few minutes)
#   --keep  keep the worktree afterwards (path is printed) for manual fixing
#   --local use the local feature branches instead of $ORIGIN_REMOTE/<branch>
#           (to verify fixes before pushing them)
#
# Exit code 0 means sync-deploy.sh should go through without manual work.
# See scripts/UPGRADING.md.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR" && git rev-parse --show-toplevel)"
CHECKS=(python3 -I "$SCRIPT_DIR/upgrade_checks.py")

# Single source of truth for the branch list and remotes: sync-deploy.sh.
eval "$(sed -n -e '/^UPSTREAM_REMOTE=/p' -e '/^ORIGIN_REMOTE=/p' \
  -e '/^ALLOW_MULTIPLE_ALEMBIC_HEADS=/p' \
  -e '/^DEPLOY_BRANCHES=(/,/^)/p' "$SCRIPT_DIR/sync-deploy.sh")"

PG_IMAGE="postgres:17.7"
REDIS_IMAGE="redis:7.4-alpine"
RUFF_VERSION="0.1.5" # keep in sync with .pre-commit-config.yaml

DO_TEST=0
KEEP=0
LOCAL=0
TAG=""
for arg in "$@"; do
  case "$arg" in
    --test) DO_TEST=1 ;;
    --keep) KEEP=1 ;;
    --local) LOCAL=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *) TAG="$arg" ;;
  esac
done

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
ok() { printf '  \033[1;32mOK\033[0m   %s\n' "$1"; }
bad() { printf '  \033[1;31mFAIL\033[0m %s\n' "$1"; FAILURES+=("$1"); }
warn() { printf '  \033[1;33mWARN\033[0m %s\n' "$1"; }
FAILURES=()

UV_BIN="$(command -v uv || true)"
[[ -n "$UV_BIN" ]] || UV_BIN="$HOME/.local/bin/uv"
[[ -x "$UV_BIN" ]] || { echo "uv not found" >&2; exit 2; }
UVX_BIN="$(dirname "$UV_BIN")/uvx"

cd "$REPO"
log "Fetching $UPSTREAM_REMOTE tags and $ORIGIN_REMOTE branches"
git fetch -q "$UPSTREAM_REMOTE" --tags --prune
git fetch -q "$ORIGIN_REMOTE" "${DEPLOY_BRANCHES[@]}"
ref() { if [[ "$LOCAL" -eq 1 ]]; then echo "refs/heads/$1"; else echo "$ORIGIN_REMOTE/$1"; fi; }
if [[ "$LOCAL" -eq 1 ]]; then
  for b in "${DEPLOY_BRANCHES[@]}"; do
    git rev-parse -q --verify "refs/heads/$b" >/dev/null || git branch -q "$b" "$ORIGIN_REMOTE/$b"
    ahead=$(git rev-list --count "$ORIGIN_REMOTE/$b..refs/heads/$b")
    behind=$(git rev-list --count "refs/heads/$b..$ORIGIN_REMOTE/$b")
    echo "  local $b: $ahead ahead, $behind behind $ORIGIN_REMOTE"
  done
fi

if [[ -z "$TAG" ]]; then
  TAG=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged "$UPSTREAM_REMOTE/master" --sort=-v:refname \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n1)
fi
echo "Upstream release: $TAG"

WT="$(mktemp -d "${TMPDIR:-/tmp}/sl-preflight.XXXXXX")"
TEST_CONTAINERS=()
cleanup() {
  for c in "${TEST_CONTAINERS[@]}"; do docker rm -f "$c" >/dev/null 2>&1; done
  if [[ "$KEEP" -eq 0 ]]; then
    git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1
    rm -rf "$WT"
  else
    echo; echo "Worktree kept at: $WT  (remove with: git worktree remove --force $WT)"
  fi
}
trap cleanup EXIT
git worktree add -q --detach "$WT" "$TAG"
cd "$WT"

# --- 1. Each branch alone on the release ------------------------------------
# Tells upstream conflicts (fix in the feature branch by merging the release
# into it) apart from conflicts between our own branches.

log "Merging each branch alone onto $TAG"
declare -A ALONE_FAILED=()
for b in "${DEPLOY_BRANCHES[@]}"; do
  git checkout -q --detach "$TAG"
  if git merge -q --no-ff --no-edit "$(ref "$b")" >/dev/null 2>&1; then
    ok "$b"
  else
    files="$(git diff --name-only --diff-filter=U | tr '\n' ' ')"
    git merge --abort
    if [[ "$files" == "uv.lock " ]]; then
      warn "$b: conflicts with $TAG only in uv.lock (sync-deploy re-locks automatically)"
    else
      ALONE_FAILED[$b]=1
      bad "$b: conflicts with $TAG in: $files-> merge $TAG into $b and resolve there"
    fi
  fi
done

# --- 2. All branches in deploy order ----------------------------------------

log "Merging all branches in deploy order (as sync-deploy.sh does)"
git checkout -q --detach "$TAG"
for b in "${DEPLOY_BRANCHES[@]}"; do
  if git merge -q --no-ff --no-edit "$(ref "$b")" >/dev/null 2>&1; then
    ok "$b"
    continue
  fi
  files="$(git diff --name-only --diff-filter=U | tr '\n' ' ')"
  if [[ "$files" == "uv.lock " ]]; then
    git checkout -q --ours uv.lock && "$UV_BIN" lock -q 2>/dev/null && git add uv.lock \
      && git commit -q --no-edit && warn "$b: uv.lock conflict auto-resolved by re-locking" \
      || { git merge --abort; bad "$b: uv.lock conflict, re-lock failed"; }
  else
    git merge --abort
    if [[ -n "${ALONE_FAILED[$b]:-}" ]]; then
      warn "$b: skipped (see above; later results are incomplete)"
    else
      bad "$b: conflicts with an earlier deploy branch in: $files-> resolve in one of the feature branches (see scripts/UPGRADING.md), not in the deploy branch"
    fi
  fi
done

# --- 3. Checks on the merged result -----------------------------------------

log "Checking merged result"
removed="$("${CHECKS[@]}" dedupe-deps)"
if [[ -n "$removed" ]]; then
  warn "duplicate dependency declarations (sync-deploy dedupes + re-locks): $(echo $removed)"
  "$UV_BIN" lock -q 2>/dev/null
fi
if "$UV_BIN" lock --check >/dev/null 2>&1; then ok "uv.lock consistent"; else bad "uv.lock does not resolve/match"; fi
for pkg in protobuf cachetools; do
  v="$(grep -A1 "^name = \"$pkg\"" uv.lock | sed -n 's/^version = "\(.*\)"/\1/p')"
  echo "       locked $pkg: ${v:-<none>}"
done
[[ "$(grep -A1 '^name = "protobuf"' uv.lock | sed -n 's/^version = "\([0-9]*\).*/\1/p')" -ge 5 ]] \
  && ok "protobuf >= 5" || bad "protobuf < 5 (event_pb2 needs >= 4.25)"

if out="$("${CHECKS[@]}" enum-dupes "$TAG")"; then ok "$out"; else bad "duplicate enum values:"; echo "$out" | sed 's/^/       /'; fi

if out="$("${CHECKS[@]}" alembic-heads)"; then
  ok "single alembic head"
elif [[ "${ALLOW_MULTIPLE_ALEMBIC_HEADS:-0}" -eq 1 ]]; then
  warn "multiple alembic heads (allowed, migration runs 'upgrade heads')"
else
  bad "multiple alembic heads -> add a merge migration in the branch owning the fork migrations"
  echo "$out" | sed 's/^/       /'
fi

if [[ -x "$UVX_BIN" ]]; then
  if out="$("$UVX_BIN" -q "ruff@$RUFF_VERSION" check . 2>&1)"; then ok "ruff check"; else bad "ruff check:"; echo "$out" | sed 's/^/       /' | head -20; fi
fi

# Compare against the release of the newest deploy branch on origin (= what
# is most likely deployed right now).
PREV=$(git for-each-ref --format='%(refname:lstrip=4)' "refs/remotes/$ORIGIN_REMOTE/deploy/" \
  | sed -n 's/^\(v[0-9]*\.[0-9]*\.[0-9]*\)-.*/\1/p' | grep -vx "$TAG" | sort -V | tail -n1)
[[ -n "$PREV" ]] || PREV=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged "$TAG" --sort=-v:refname \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | grep -vx "$TAG" | head -n1)
echo
echo "New upstream migrations $PREV..$TAG (check for extensions, long index builds, data changes):"
git diff --name-only --diff-filter=A "$PREV" "$TAG" -- migrations/versions | sed 's/^/  /'
echo "New/changed dependencies ($PREV..$TAG):"
git diff "$PREV" "$TAG" -- pyproject.toml | grep -E '^[+-]\s+"' | sed 's/^/  /'

# --- 4. Optional: migrations + tests -----------------------------------------

if [[ "$DO_TEST" -eq 1 && ${#FAILURES[@]} -eq 0 ]]; then
  log "Running migrations and tests ($PG_IMAGE, $REDIS_IMAGE)"
  for port in 15432 6379; do
    if ss -ltn | grep -q ":$port "; then bad "port $port already in use, cannot start test containers"; fi
  done
  if [[ ${#FAILURES[@]} -eq 0 ]]; then
    TEST_CONTAINERS=(sl-preflight-db sl-preflight-redis)
    docker run -d --name sl-preflight-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test \
      -e POSTGRES_DB=test -p 127.0.0.1:15432:5432 "$PG_IMAGE" >/dev/null
    docker run -d --name sl-preflight-redis -p 127.0.0.1:6379:6379 "$REDIS_IMAGE" >/dev/null
    for _ in $(seq 30); do
      docker exec sl-preflight-db pg_isready -U test -q 2>/dev/null && break
      sleep 1
    done
    "$UV_BIN" sync -q --locked
    UPGRADE_TARGET=head
    [[ "${ALLOW_MULTIPLE_ALEMBIC_HEADS:-0}" -eq 1 ]] && UPGRADE_TARGET=heads
    if CONFIG=tests/test.env "$UV_BIN" run alembic upgrade "$UPGRADE_TARGET" >"$WT.alembic.log" 2>&1; then
      ok "alembic upgrade $UPGRADE_TARGET"
      if "$UV_BIN" run pytest -c pytest.ci.ini -q -p no:cacheprovider -W ignore >"$WT.pytest.log" 2>&1; then
        ok "pytest: $(grep -E '[0-9]+ passed' "$WT.pytest.log" | tail -1)"
      else
        bad "pytest failed, see $WT.pytest.log"
        grep -E '^(FAILED|ERROR)' "$WT.pytest.log" | head -20 | sed 's/^/       /'
      fi
    else
      bad "alembic upgrade failed, see $WT.alembic.log"
    fi
  fi
elif [[ "$DO_TEST" -eq 1 ]]; then
  warn "skipping tests because of the failures above"
fi

# --- Summary ------------------------------------------------------------------

echo
if [[ ${#FAILURES[@]} -eq 0 ]]; then
  printf '\033[1;32mPreflight OK for %s\033[0m - scripts/sync-deploy.sh should run through.\n' "$TAG"
  exit 0
fi
printf '\033[1;31m%d problem(s) for %s:\033[0m\n' "${#FAILURES[@]}" "$TAG"
printf '  - %s\n' "${FAILURES[@]}"
exit 1
