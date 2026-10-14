#!/usr/bin/env bash
# Keep GitHub (mirror source) on top of GitLab main. GitLab is where auto-merge creates merge commits,
# GitHub is where we push; the mirror only fast-forwards. Run before every push:
#   bash scripts/sync_main.sh && git push --force-with-lease origin main
#
# Guard: a push to main auto-cancels the showcase's running main pipeline (2026-10-14: issue #4's journey
# pipeline 2945742313 was cancelled at 07:40:38Z by our own push and had to be retried). So this refuses to
# proceed while a showcase main pipeline is running or pending. Override (you accept the cancel):
#   bash scripts/sync_main.sh --allow-running      or      SYNC_ALLOW_RUNNING=1 bash scripts/sync_main.sh
set -euo pipefail
SHOWCASE_API="https://gitlab.com/api/v4/projects/gitlab-ai-hackathon%2Ftranscend-october-2026%2F43190989%2Fshowcase"
if [ "${1:-}" != "--allow-running" ] && [ "${SYNC_ALLOW_RUNNING:-0}" != "1" ]; then
  busy=""
  for st in created waiting_for_resource preparing pending running; do
    if ! out=$(curl -fsS "$SHOWCASE_API/pipelines?ref=main&status=$st&per_page=20"); then
      echo "cannot read showcase pipelines (status=$st); refusing to push blind (--allow-running to override)" >&2
      exit 3
    fi
    busy+=$(printf '%s' "$out" | python -c "import json,sys;print(' '.join(f\"{p['id']}({p['status']})\" for p in json.load(sys.stdin)))")" "
  done
  if [ -n "${busy// /}" ]; then
    echo "showcase main pipeline(s) still active: ${busy}- a push to main would cancel them." >&2
    echo "wait until they finish, or re-run with --allow-running" >&2
    exit 2
  fi
  echo "no showcase main pipeline running or pending"
fi
git remote get-url gitlab >/dev/null 2>&1 || git remote add gitlab https://gitlab.com/hanwg666/gitlab-autopilot.git
git fetch -q gitlab main
if git merge-base --is-ancestor gitlab/main HEAD; then
  echo "main already contains gitlab/main ($(git rev-parse --short gitlab/main))"
else
  echo "rebasing onto gitlab/main ($(git rev-parse --short gitlab/main))"
  git -c user.name="Han Vigo" -c user.email=hanwg666@gmail.com rebase gitlab/main
fi

# The judged project (showcase) also merges on its own (Autopilot auto-merge); merge it in too so the
# mirror push fast-forwards there as well.
git remote get-url showcase >/dev/null 2>&1 || git remote add showcase https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43190989/showcase.git
git fetch -q showcase main
if git merge-base --is-ancestor showcase/main HEAD; then
  echo "main already contains showcase/main ($(git rev-parse --short showcase/main))"
else
  echo "merging showcase/main ($(git rev-parse --short showcase/main))"
  git -c user.name="Han Vigo" -c user.email=284575144+vigohan95@users.noreply.github.com merge --no-edit showcase/main
fi
