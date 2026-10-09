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
