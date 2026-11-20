"""Tests for app/github/client.py (Stage D).

httpx.MockTransport plays the part of GitHub: each test gives it a small function
that answers requests, so nothing touches the network and no token is needed.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.github.client import (
    API,
    GitHubClient,
    GitHubError,
    failed_job,
    make_diff_fetcher,
    make_log_fetcher,
    to_step_labelled_log,
)
from app.models.evidence import EvidenceBundle
from app.tools.log_extract import extract_failure, parse_log

LOGS = Path(__file__).parents[1] / "fixtures" / "logs"
RECORDED = Path(__file__).parents[1] / "fixtures" / "github"
REPO = "mee-tan/demo-repo-recovery-agent-target"
SHA = "b" * 40


def client_for(handler, sleeps: list | None = None) -> GitHubClient:
    http = httpx.Client(base_url=API, transport=httpx.MockTransport(handler), follow_redirects=True)
    sleep = sleeps.append if sleeps is not None else (lambda seconds: None)
    return GitHubClient("test-token", http=http, sleep=sleep)


def api_style(gh_log: str) -> str:
    """Turn a `gh run view --log` file into what the API returns: drop job and step."""
    return "\n".join(line.split("\t", 2)[2] for line in gh_log.splitlines())


def steps_of(gh_log: str, skipped_after_lint: tuple[str, ...] = ()) -> list[dict]:
    names: list[str] = []
    for line in gh_log.splitlines():
        step = line.split("\t")[1]
        if step not in names:
            names.append(step)
    steps = []
    for name in names:
        steps.append({"name": name, "conclusion": "success"})
        if name == "Lint":
            steps += [{"name": s, "conclusion": "skipped"} for s in skipped_after_lint]
    for number, step in enumerate(steps, start=1):
        step["number"] = number
    return steps


# ---------- requests ----------

def test_requests_carry_the_token_and_api_version():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": 5, "head_sha": SHA})

    run = client_for(handler).get_run(REPO, 5)
    assert run["head_sha"] == SHA
    assert seen[0].url.path == f"/repos/{REPO}/actions/runs/5"
    assert seen[0].headers.get("authorization") == "Bearer test-token"
    assert seen[0].headers.get("x-github-api-version") == "2022-11-28"


def test_jobs_asks_for_the_latest_attempt():
    def handler(request):
        assert request.url.params.get("filter") == "latest"
        return httpx.Response(200, json={"jobs": [{"id": 1, "conclusion": "failure"}]})

    assert client_for(handler).get_jobs(REPO, 5) == [{"id": 1, "conclusion": "failure"}]


def test_log_download_follows_the_redirect_without_leaking_the_token():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://logs.example.net/job.txt"})
        return httpx.Response(200, text="2026-11-12T23:05:41.0Z hello")

    assert client_for(handler).get_job_log(REPO, 9) == "2026-11-12T23:05:41.0Z hello"
    assert seen[1].url.host == "logs.example.net"
    assert seen[1].headers.get("authorization") is None  # token stays with GitHub


def test_commit_files():
    def handler(request):
        assert request.url.path == f"/repos/{REPO}/commits/{SHA}"
        return httpx.Response(200, json={"files": [{"filename": "a.py", "patch": "@@ x"}]})

    assert client_for(handler).get_commit_files(REPO, SHA) == [
        {"filename": "a.py", "patch": "@@ x"}
    ]


# ---------- errors and retries ----------

def test_server_errors_are_retried_then_succeed():
    answers = [503, 502, 200]
    sleeps: list = []

    def handler(request):
        status = answers.pop(0)
        return httpx.Response(status, json={"id": 1} if status == 200 else {})

    assert client_for(handler, sleeps).get_run(REPO, 1) == {"id": 1}
    assert sleeps == [1, 2]  # waited 1 s, then 2 s


def test_retries_give_up_eventually():
    sleeps: list = []
    with pytest.raises(GitHubError) as error:
        client_for(lambda request: httpx.Response(503), sleeps).get_run(REPO, 1)
    assert error.value.status == 503
    assert len(sleeps) == 3


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_missing_errors_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    with pytest.raises(GitHubError):
        client_for(handler, []).get_run(REPO, 1)
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["no-slash", "a/b/c", "a/../b", "a b/c", ""])
def test_bad_repository_names_are_refused_before_any_request(bad):
    def handler(request):
        raise AssertionError("no request should be made")

    with pytest.raises(ValueError):
        client_for(handler).get_run(bad, 1)


def test_bad_sha_is_refused():
    with pytest.raises(ValueError):
        client_for(lambda r: httpx.Response(200)).get_commit_files(REPO, "main")


# ---------- turning the API log back into the step-labelled format ----------

@pytest.mark.parametrize(
    ("name", "skipped"),
    [("lint_unused_import", ("Test",)), ("import_error", ()), ("logic_bug", ())],
)
@pytest.mark.parametrize("keep_bom", [True, False])
def test_step_labels_are_rebuilt_exactly(name, skipped, keep_bom):
    original = (LOGS / f"{name}.log").read_text(encoding="utf-8")
    raw = api_style(original)
    if not keep_bom:
        raw, original = raw.replace("﻿", ""), original.replace("﻿", "")
    job = {"name": "test", "steps": steps_of(original, skipped)}
    assert to_step_labelled_log(job, raw).splitlines() == original.splitlines()


def test_failed_job_picks_the_first_failure():
    jobs = [{"id": 1, "conclusion": "success"}, {"id": 2, "conclusion": "failure"}]
    assert failed_job(jobs)["id"] == 2
    assert failed_job([{"id": 1, "conclusion": "success"}]) is None


# ---------- the fetchers the graph uses ----------

def test_log_fetcher_end_to_end_with_a_fake_github():
    original = (LOGS / "logic_bug.log").read_text(encoding="utf-8")
    job = {"id": 77, "name": "test", "conclusion": "failure", "steps": steps_of(original)}
    jobs = {"jobs": [job]}

    def handler(request):
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=jobs)
        if request.url.path.endswith("/actions/jobs/77/logs"):
            return httpx.Response(200, text=api_style(original))
        return httpx.Response(404)

    log = make_log_fetcher(client_for(handler))(REPO, 5)
    assert parse_log(log)[0].step == "Set up job"
    bundle = EvidenceBundle(repository=REPO, run_id=5, head_sha=SHA)
    result = extract_failure(log, bundle)
    assert result.failed_step == "Test"
    assert "assert -2 == 2" in bundle.items[0].text


def test_log_fetcher_refuses_a_run_without_failures():
    handler = lambda request: httpx.Response(200, json={"jobs": [{"conclusion": "success"}]})  # noqa: E731
    with pytest.raises(LookupError):
        make_log_fetcher(client_for(handler))(REPO, 5)


def test_diff_fetcher_compares_with_main_and_keeps_only_what_the_graph_needs():
    files = [{"filename": "src/calculator/ops.py", "status": "modified", "patch": "@@ -1 +1 @@",
              "sha": "x", "additions": 1, "blob_url": "https://..."}]
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"files": files})

    assert make_diff_fetcher(client_for(handler))(REPO, SHA) == [
        {"filename": "src/calculator/ops.py", "status": "modified", "patch": "@@ -1 +1 @@"}
    ]
    assert seen == [f"/repos/{REPO}/compare/main...{SHA}"]


def test_diff_fetcher_falls_back_to_the_commit_when_on_main():
    commit_files = [{"filename": "src/calculator/ops.py", "patch": "@@ x"}]

    def handler(request):
        if "/compare/" in request.url.path:
            return httpx.Response(200, json={"files": []})  # nothing differs from main
        return httpx.Response(200, json={"files": commit_files})

    fetched = make_diff_fetcher(client_for(handler))(REPO, SHA)
    assert [f["filename"] for f in fetched] == ["src/calculator/ops.py"]


@pytest.mark.parametrize("bad", ["main..evil", "a b", "x;y"])
def test_bad_compare_refs_are_refused(bad):
    with pytest.raises(ValueError):
        client_for(lambda r: httpx.Response(200)).get_compare_files(REPO, bad, SHA)


# ---------- real API answers you recorded with `gh api` (section 5 of the walkthrough) ----------

@pytest.mark.skipif(not (RECORDED / "logic_bug_jobs.json").exists(), reason="not recorded yet")
def test_recorded_real_api_log_is_understood():
    jobs = json.loads((RECORDED / "logic_bug_jobs.json").read_text(encoding="utf-8"))["jobs"]
    raw = (RECORDED / "logic_bug_job.log").read_text(encoding="utf-8")
    job = failed_job(jobs)
    assert job is not None
    log = to_step_labelled_log(job, raw)
    bundle = EvidenceBundle(repository=REPO, run_id=1, head_sha=SHA)
    result = extract_failure(log, bundle)
    assert result.failed_step == "Test"
    assert result.command == "pytest"
    assert "assert -2 == 2" in "\n".join(item.text for item in bundle.items)
