#!/usr/bin/env bash
#
# Sync this fork's master with the latest upstream SimpleLogin release, build a
# deploy branch on top of it with our own feature branches merged in, and
# build+push the resulting Docker image to our private registry.
#
# Usage:
#   scripts/sync-deploy.sh              # full run: sync, merge, build, push
#   scripts/sync-deploy.sh --no-push    # do everything locally, push nothing
#                                        # (no git push, no docker push)
#   scripts/sync-deploy.sh --no-build   # only do the git sync/merge, skip docker
#
# After this script finishes, update the image tag in Portainer manually.

set -euo pipefail

# --- Configuration ----------------------------------------------------------

UPSTREAM_REMOTE="upstream"
UPSTREAM_URL="https://github.com/simple-login/app.git"
ORIGIN_REMOTE="origin"
MAIN_BRANCH="master"

# Feature branches (on $ORIGIN_REMOTE) to merge into the deploy branch, in
# order. Edit this list whenever the set of features to ship changes.
DEPLOY_BRANCHES=(
  "feature/user-blacklists"
  "fix/uv-lock-protobuf-conflict"
  "feature/admin-user-bulk-delete-lifetime"
  "feature/admin-create-user"
)

# 1 = the migration container runs `alembic upgrade heads` (plural; set in
# /opt/arcane-projects/simplelogin/compose.yaml since v4.83.5-cb), so multiple
# alembic heads (fork migrations + new upstream migrations) are fine and no
# merge migration is needed anymore. Set back to 0 if it ever runs
# `upgrade head` (singular) again - that fails with more than one head.
ALLOW_MULTIPLE_ALEMBIC_HEADS=1

IMAGE_NAME="reg.hosrv.de/library/sl-app"
TAG_SUFFIX="-cb"
PLATFORM="linux/amd64"

# --- Flags -------------------------------------------------------------------

DO_PUSH=1
DO_BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-push) DO_PUSH=0 ;;
    --no-build) DO_BUILD=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

# --- Helpers -----------------------------------------------------------------

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

find_uv() {
  UV_BIN="$(command -v uv || true)"
  [[ -n "$UV_BIN" ]] || UV_BIN="$HOME/.local/bin/uv"
  [[ -x "$UV_BIN" ]] || die "uv not found (checked PATH and \$HOME/.local/bin/uv)."
}

require_clean_worktree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    die "Working tree is not clean. Commit or stash your changes first."
  fi
}

# --- Re-exec from outside the repo ---------------------------------------------
#
# This script only exists on the build branch, and it checks out other branches
# while running. bash reads scripts incrementally, so it would vanish (or change)
# under its own feet. Copy it (and its Python helper) next to the repo and run
# that copy instead.

if [[ -z "${SYNC_DEPLOY_REPO:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  SYNC_DEPLOY_REPO="$(cd "$SCRIPT_DIR" && git rev-parse --show-toplevel)"
  SELF_COPY="$(dirname "$SYNC_DEPLOY_REPO")/sync-deploy.sh"
  HELPER_COPY="$(dirname "$SYNC_DEPLOY_REPO")/sync-deploy-upgrade_checks.py"
  cp "${BASH_SOURCE[0]}" "$SELF_COPY"
  cp "$SCRIPT_DIR/upgrade_checks.py" "$HELPER_COPY"
  chmod +x "$SELF_COPY"
  log "Re-running from $SELF_COPY"
  SYNC_DEPLOY_REPO="$SYNC_DEPLOY_REPO" SYNC_DEPLOY_HELPER="$HELPER_COPY" exec "$SELF_COPY" "$@"
fi
CHECKS=(python3 -I "$SYNC_DEPLOY_HELPER")

# --- Preconditions -------------------------------------------------------------

cd "$SYNC_DEPLOY_REPO"
require_clean_worktree

if ! git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  log "Adding '$UPSTREAM_REMOTE' remote ($UPSTREAM_URL)"
  git remote add "$UPSTREAM_REMOTE" "$UPSTREAM_URL"
fi

log "Fetching tags from $UPSTREAM_REMOTE"
git fetch "$UPSTREAM_REMOTE" --tags --prune

LATEST_TAG=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged "$UPSTREAM_REMOTE/master" --sort=-v:refname \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n1)
[[ -n "$LATEST_TAG" ]] || die "Could not determine the latest upstream release tag."
log "Latest upstream release: $LATEST_TAG"

MYTAG="${LATEST_TAG}${TAG_SUFFIX}"
DEPLOY_BRANCH="deploy/${MYTAG}"

# --- Sync master with upstream -------------------------------------------------

log "Fast-forwarding local '$MAIN_BRANCH' to $LATEST_TAG"
git checkout "$MAIN_BRANCH"
git merge --ff-only "$LATEST_TAG"

if [[ "$DO_PUSH" -eq 1 ]]; then
  log "Pushing '$MAIN_BRANCH' to $ORIGIN_REMOTE"
  git push "$ORIGIN_REMOTE" "$MAIN_BRANCH"
else
  log "Skipping push of '$MAIN_BRANCH' (--no-push)"
fi

# --- Build the deploy branch ---------------------------------------------------

log "Fetching feature branches from $ORIGIN_REMOTE"
git fetch "$ORIGIN_REMOTE" "${DEPLOY_BRANCHES[@]}"

log "(Re)creating '$DEPLOY_BRANCH' from $LATEST_TAG"
git checkout -B "$DEPLOY_BRANCH" "$LATEST_TAG"

for branch in "${DEPLOY_BRANCHES[@]}"; do
  log "Merging '$branch' into '$DEPLOY_BRANCH'"
  if ! git merge --no-ff --no-edit "$ORIGIN_REMOTE/$branch" -m "Merge '$branch' into $DEPLOY_BRANCH"; then
    UNMERGED="$(git diff --name-only --diff-filter=U)"
    if [[ "$UNMERGED" == "uv.lock" ]]; then
      # uv.lock is generated: take our side and re-lock against the merged
      # pyproject.toml.
      log "Only uv.lock conflicted - resolving by re-locking"
      find_uv
      git checkout --ours uv.lock
      "$UV_BIN" lock
      git add uv.lock
      git commit --no-edit
    else
      git merge --abort
      die "Conflict merging '$branch' in: $(echo $UNMERGED). Do NOT resolve this in the deploy branch (it is rebuilt from scratch on every run): merge $LATEST_TAG into '$branch', resolve it there, push '$branch' and re-run. Run scripts/upgrade-preflight.sh to see all conflicts at once. See scripts/UPGRADING.md."
    fi
  fi
done

# --- Dedupe dependency declarations that may collide across merges -----------
#
# Two branches can independently declare the same pyproject.toml dependency
# with different constraints (each correct in isolation, e.g. because both
# fix the same upstream issue - see fix/uv-lock-protobuf-conflict and
# feature/user-blacklists both declaring 'cachetools'). That doesn't cause a
# git conflict since the lines usually live in different parts of the
# dependencies array, but it leaves two constraints for the same package.
# Keep the first (topmost) declaration and drop later duplicates, then
# re-lock.

log "Checking for duplicate pyproject.toml dependency declarations"
DEDUPE_OUTPUT=$("${CHECKS[@]}" dedupe-deps)

if [[ -n "$DEDUPE_OUTPUT" ]]; then
  log "Removed duplicate dependency declaration(s), re-locking:"
  echo "$DEDUPE_OUTPUT" | sed 's/^/  - /'
  find_uv
  "$UV_BIN" lock
  git add pyproject.toml uv.lock
  git commit -m "Dedupe pyproject.toml dependencies after branch merge

Two of the merged branches declared the same dependency with different
version constraints; keep the first (topmost) declaration and re-lock."
fi

# --- Sanity checks on the merged result --------------------------------------

log "Checking merged result"
find_uv
"$UV_BIN" lock --check || die "uv.lock does not match pyproject.toml."
"${CHECKS[@]}" enum-dupes "$LATEST_TAG" || die "Duplicate enum values (silent aliases!) - probably a fork value collides with a new upstream one. Renumber the fork value (fork values live at 1000+)."
if ! "${CHECKS[@]}" alembic-heads; then
  [[ "$ALLOW_MULTIPLE_ALEMBIC_HEADS" -eq 1 ]] \
    || die "Multiple alembic heads. Either add a merge migration in the branch owning the fork migrations, or switch the migration container to 'alembic upgrade heads' and set ALLOW_MULTIPLE_ALEMBIC_HEADS=1."
  log "Multiple alembic heads allowed (ALLOW_MULTIPLE_ALEMBIC_HEADS=1)"
fi

if [[ "$DO_PUSH" -eq 1 ]]; then
  log "Pushing '$DEPLOY_BRANCH' to $ORIGIN_REMOTE (force, since it's rebuilt from scratch each run)"
  git push "$ORIGIN_REMOTE" "$DEPLOY_BRANCH" --force-with-lease
else
  log "Skipping push of '$DEPLOY_BRANCH' (--no-push)"
fi

# --- Docker build & push --------------------------------------------------------

if [[ "$DO_BUILD" -eq 1 ]]; then
  log "Building Docker image $IMAGE_NAME:$MYTAG ($PLATFORM)"
  BUILD_ARGS=(
    buildx build
    --platform "$PLATFORM"
    -t "$IMAGE_NAME:$MYTAG"
    -t "$IMAGE_NAME:latest"
  )
  if [[ "$DO_PUSH" -eq 1 ]]; then
    BUILD_ARGS+=(--push)
  else
    BUILD_ARGS+=(--load)
  fi
  docker "${BUILD_ARGS[@]}" .
else
  log "Skipping Docker build (--no-build)"
fi

log "Done."
echo "Deploy branch: $DEPLOY_BRANCH"
echo "Image tag:     $MYTAG"
echo
echo "Update the image tag in Portainer to '$MYTAG' to deploy."
