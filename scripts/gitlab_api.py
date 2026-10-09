"""Minimal GitLab REST helper shared by the Autopilot CI scripts.

Credentials are taken from the environment and never printed:

* ``AUTOPILOT_TOKEN`` — project access token (scope ``api``), masked CI variable.
  Needed for everything that CI_JOB_TOKEN cannot do (issues, notes, merges).
* ``CI_JOB_TOKEN`` — used only for endpoints that accept it (pipeline triggers,
  releases, package/container registries).
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx


class GitLab:
    def __init__(self, token: str | None = None, base: str | None = None, project: str | None = None) -> None:
        self.base = (base or os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")).rstrip("/")
        self.project = project or os.environ.get("CI_PROJECT_ID", "")
        self.token = token if token is not None else os.environ.get("AUTOPILOT_TOKEN", "")
        self.job_token = os.environ.get("CI_JOB_TOKEN", "")
        headers = {"Accept": "application/json"}
        if self.token:
            if os.environ.get("AUTOPILOT_TOKEN_TYPE", "").lower() == "bearer":
                headers["Authorization"] = f"Bearer {self.token}"
            else:
                headers["PRIVATE-TOKEN"] = self.token
        elif self.job_token:
            headers["JOB-TOKEN"] = self.job_token  # accepted by a subset of endpoints
        self._client = httpx.Client(base_url=self.base, headers=headers, timeout=30)
        # GETs on a public project also work anonymously; used as a fallback when a token is refused.
        self._anon = httpx.Client(base_url=self.base, headers={"Accept": "application/json"}, timeout=30)

    @property
    def can_write(self) -> bool:
        return bool(self.token)

    # -- low level -----------------------------------------------------------
    def _url(self, path: str) -> str:
        if path.startswith("/projects/") or path.startswith("/ai/") or path.startswith("/groups/"):
            return path
        return f"/projects/{self.project}/{path.lstrip('/')}"

    def _get_raw(self, path: str, params: dict | None = None) -> httpx.Response:
        r = self._client.get(self._url(path), params=params)
        if r.status_code in (401, 403) and (self.token or self.job_token):
            r = self._anon.get(self._url(path), params=params)
        r.raise_for_status()
        return r

    def get(self, path: str, **params: Any) -> Any:
        r = self._get_raw(path, params)
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    def get_bytes(self, path: str, **params: Any) -> bytes:
        return self._get_raw(path, params).content

    def get_all(self, path: str, **params: Any) -> list[Any]:
        out: list[Any] = []
        page = 1
        while True:
            r = self._get_raw(path, {**params, "per_page": 100, "page": page})
            chunk = r.json()
            out.extend(chunk)
            if not r.headers.get("x-next-page"):
                return out
            page += 1

    def post(self, path: str, json: dict | None = None, data: dict | None = None) -> Any:
        r = self._client.post(self._url(path), json=json, data=data)
        r.raise_for_status()
        return r.json() if r.content else None

    def put(self, path: str, json: dict | None = None) -> Any:
        r = self._client.put(self._url(path), json=json)
        r.raise_for_status()
        return r.json() if r.content else None

    # -- helpers used by more than one script ----------------------------------
    def project_web_url(self) -> str:
        return os.environ.get("CI_PROJECT_URL") or self.get(f"/projects/{self.project}")["web_url"]

    def issue_note(self, iid: int, body: str) -> Any:
        return self.post(f"issues/{iid}/notes", json={"body": body})

    def mr_note(self, iid: int, body: str) -> Any:
        return self.post(f"merge_requests/{iid}/notes", json={"body": body})

    def add_labels(self, kind: str, iid: int, labels: list[str]) -> Any:
        return self.put(f"{kind}/{iid}", json={"add_labels": ",".join(labels)})

    def trigger_pipeline(self, ref: str, variables: dict[str, str]) -> Any:
        """Start a pipeline with CI_JOB_TOKEN (allowed for the same project)."""
        data = {"token": self.job_token or self.token, "ref": ref}
        for k, v in variables.items():
            data[f"variables[{k}]"] = v
        r = httpx.post(f"{self.base}/projects/{self.project}/trigger/pipeline", data=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def releases(self) -> list[dict]:
        return self.get_all("releases")


def warn(msg: str) -> None:
    print(f"::warning:: {msg}", file=sys.stderr)
