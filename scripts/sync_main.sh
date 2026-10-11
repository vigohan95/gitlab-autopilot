#!/usr/bin/env bash
# Keep GitHub (mirror source) on top of GitLab main. GitLab is where auto-merge creates merge commits,
# GitHub is where we push; the mirror only fast-forwards. Run before every push:
#   bash scripts/sync_main.sh && git push --force-with-lease origin main
set -euo pipefail
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
