#!/bin/sh
# Run a registry-bound command (docker login/build/push) and retry it only when it failed for a transient
# registry or network reason. POSIX sh: the build-image job runs in docker:27 (Alpine, busybox).
#
#   sh scripts/registry_retry.sh docker push "$IMAGE:$CI_COMMIT_SHA"
#
# Why: 2026-10-14 drill, run 5. MR pipeline 2945797046 failed at build-image with "unexpected status from HEAD
# request to <registry>: 500 Internal Server Error" (GitLab container registry, transient). GitLab classifies a
# registry error inside the script as script_failure, which the job-level `retry: when:` list cannot match without
# also retrying real failures, so the retry lives here and looks at the command's output.
#
# Up to REGISTRY_RETRY_ATTEMPTS attempts (default 3), waiting REGISTRY_RETRY_BACKOFF seconds (default 10) and then
# doubling. Any other failure (a broken Dockerfile, a denied push, a failing test inside the build) exits at once
# with the command's own exit code.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: sh scripts/registry_retry.sh <command> [args...]" >&2
  exit 64
fi

attempts="${REGISTRY_RETRY_ATTEMPTS:-3}"
delay="${REGISTRY_RETRY_BACKOFF:-10}"
# registry 5xx, "unexpected status", TLS and network timeouts; matched case-insensitively
TRANSIENT='unexpected status|(status|code)[: ]+5[0-9][0-9]|5[0-9][0-9] (internal server error|bad gateway|service unavailable|gateway time-?out)|tls handshake timeout|i/o timeout|net/http: request canceled|context deadline exceeded|connection reset by peer|client\.timeout exceeded'

log=$(mktemp)
rcfile=$(mktemp)
trap 'rm -f "$log" "$rcfile"' EXIT

n=1
while :; do
  # stream the output (docker build is long) and keep a copy; the pipe hides the exit code, so write it to a file
  { "$@" 2>&1; echo "$?" >"$rcfile"; } | tee "$log"
  rc=$(cat "$rcfile")
  if [ "$rc" -eq 0 ]; then
    exit 0
  fi
  if ! grep -Eiq "$TRANSIENT" "$log"; then
    exit "$rc"
  fi
  if [ "$n" -ge "$attempts" ]; then
    echo "registry_retry: transient registry/network error on all $attempts attempts: $*" >&2
    exit "$rc"
  fi
  echo "registry_retry: attempt $n/$attempts failed with a transient registry/network error (exit $rc); retrying in ${delay}s: $*" >&2
  sleep "$delay"
  delay=$((delay * 2))
  n=$((n + 1))
done
