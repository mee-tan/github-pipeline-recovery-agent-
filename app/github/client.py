"""GitHub REST client (Stage D): read a failed run, its job log and its commit diff.

Read-only. Authenticates with a token passed in by the caller (for development,
`gh auth token`; for the demo, a fine-grained token with read access to one repo).
"""

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

API = "https://api.github.com"
REPO_NAME = re.compile(r"^[\w.-]+/[\w.-]+$")
RETRYABLE = {429, 500, 502, 503, 504}  # rate limited, or GitHub having a bad moment
MAX_LOG_CHARS = 2_000_000


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # follow_redirects: the log endpoint answers with a redirect to a download URL.
        # httpx drops the Authorization header when a redirect goes to another host,
        # so the token is never sent to the download server.
        self._http = http or httpx.Client(base_url=API, timeout=30.0, follow_redirects=True)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._max_retries = max_retries
        self._sleep = sleep

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            response = self._http.get(path, params=params, headers=self._headers)
            if response.status_code in RETRYABLE and attempt < self._max_retries:
                self._sleep(2**attempt)  # wait 1 s, 2 s, 4 s ...
                continue
            if response.status_code >= 400:
                # 401, 403, 404 and friends are never retried: trying again won't help.
                raise GitHubError(response.status_code, f"GET {path} -> {response.status_code}")
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _repo(repository: str) -> str:
        # Refuse anything that isn't exactly "owner/name", so a crafted value can't
        # point the request somewhere else (for example "a/b/../../users").
        if not REPO_NAME.match(repository) or ".." in repository:
            raise ValueError(f"invalid repository name: {repository!r}")
        return repository

    def get_run(self, repository: str, run_id: int) -> dict[str, Any]:
        return self._get(f"/repos/{self._repo(repository)}/actions/runs/{int(run_id)}").json()

    def get_jobs(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        path = f"/repos/{self._repo(repository)}/actions/runs/{int(run_id)}/jobs"
        return self._get(path, params={"filter": "latest", "per_page": 100}).json()["jobs"]

    def get_job_log(self, repository: str, job_id: int) -> str:
        path = f"/repos/{self._repo(repository)}/actions/jobs/{int(job_id)}/logs"
        return self._get(path).text[:MAX_LOG_CHARS]

    def get_commit_files(self, repository: str, sha: str) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"invalid commit sha: {sha!r}")
        commit = self._get(f"/repos/{self._repo(repository)}/commits/{sha}").json()
        return commit.get("files", [])

    def get_compare_files(self, repository: str, base: str, head: str) -> list[dict[str, Any]]:
        """Files changed on `head` since it split from `base` (like a pull request's diff)."""
        for ref in (base, head):
            if not re.fullmatch(r"[\w./-]+", ref) or ".." in ref:
                raise ValueError(f"invalid ref: {ref!r}")
        path = f"/repos/{self._repo(repository)}/compare/{base}...{head}"
        return self._get(path).json().get("files", [])


# ---------- turning API answers into what the graph needs ----------

STEP_START_MARKERS = ("##[group]Run ", "Post job cleanup.", "Cleaning up orphan processes")
BOM = "﻿"


def failed_job(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first job that failed, or None."""
    return next((job for job in jobs if job.get("conclusion") == "failure"), None)


def to_step_labelled_log(job: dict[str, Any], raw_log: str) -> str:
    """Rebuild the `gh run view --log` format: "job<TAB>step<TAB>timestamp message".

    The API's job log has no step names, but each step's output begins with one of
    STEP_START_MARKERS (or an invisible BOM). Skipped steps print nothing, so they are
    left out of the step list before lines are matched to steps in order.
    """
    steps = [
        step["name"]
        for step in sorted(job.get("steps", []), key=lambda s: s["number"])
        if step.get("conclusion") != "skipped"
    ] or ["unknown step"]
    index = 0
    labelled = []
    for number, line in enumerate(raw_log.splitlines()):
        _timestamp, _, message = line.lstrip(BOM).partition(" ")
        starts_step = line.startswith(BOM) or message.startswith(STEP_START_MARKERS)
        if number > 0 and starts_step and index + 1 < len(steps):
            index += 1
        labelled.append(f"{job['name']}\t{steps[index]}\t{line}")
    return "\n".join(labelled)


def make_log_fetcher(client: GitHubClient) -> Callable[[str, int], str]:
    """A fetch_log for the graph's Dependencies: the failed job's log, step-labelled."""

    def fetch_log(repository: str, run_id: int) -> str:
        job = failed_job(client.get_jobs(repository, run_id))
        if job is None:
            raise LookupError(f"run {run_id} has no failed job")
        return to_step_labelled_log(job, client.get_job_log(repository, job["id"]))

    return fetch_log


def make_diff_fetcher(
    client: GitHubClient, base_branch: str = "main"
) -> Callable[[str, str], list[dict[str, Any]]]:
    """A fetch_diff for the graph's Dependencies: changed files and their patches.

    Compares the failing commit with the base branch, so a merge commit on a feature
    branch still shows the branch's real changes. If that comparison is empty (the
    failing commit IS on the base branch), it falls back to the commit's own changes.
    """

    def fetch_diff(repository: str, sha: str) -> list[dict[str, Any]]:
        files = client.get_compare_files(repository, base_branch, sha)
        if not files:
            files = client.get_commit_files(repository, sha)
        return [
            {"filename": f["filename"], "status": f.get("status"), "patch": f.get("patch", "")}
            for f in files
        ]

    return fetch_diff
