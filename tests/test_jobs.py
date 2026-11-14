"""JobService unit tests: TTL, de-duplication, limits, thread safety."""

import threading
import time

import pytest

from app.schemas.jobs import JobKind, JobStatus
from app.schemas.spectrum import SpectrumResponse
from app.services.jobs import JobService, TooManyJobs

RESULT = SpectrumResponse(
    source="target", target_name="x", ra_deg=1, dec_deg=1, epoch_id="e",
    is_cached_golden_path=False, spectrum_102_bands=[],
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(**kw):
    defaults = dict(ttl_s=60, max_running=1, max_active=4)
    return JobService(**{**defaults, **kw})


def test_lifecycle_and_dedup():
    jobs = make()
    job, created = jobs.find_or_create(JobKind.SPECTRUM, "k")
    assert created and job.status is JobStatus.QUEUED
    again, created = jobs.find_or_create(JobKind.SPECTRUM, "k")
    assert not created and again.job_id == job.job_id
    other, created = jobs.find_or_create(JobKind.SKY_PATCH, "k")  # kind is part of the key
    assert created and other.job_id != job.job_id

    jobs.run(job.job_id, lambda: RESULT)
    done = jobs.get(job.job_id)
    assert done.status is JobStatus.COMPLETED and done.result == RESULT
    assert done.updated_at >= done.created_at


def test_failure_is_reported_and_not_reused():
    jobs = make()
    job, _ = jobs.find_or_create(JobKind.SPECTRUM, "k")

    def boom():
        raise ValueError("IRSA said no " + "x" * 1000)

    jobs.run(job.job_id, boom)
    failed = jobs.get(job.job_id)
    assert failed.status is JobStatus.FAILED
    assert failed.error.startswith("ValueError: IRSA said no") and len(failed.error) <= 300
    retry, created = jobs.find_or_create(JobKind.SPECTRUM, "k")
    assert created and retry.job_id != job.job_id


def test_finished_jobs_expire_after_ttl():
    clock, expired = Clock(), []
    jobs = make(clock=clock, on_expire=expired.append)
    job, _ = jobs.find_or_create(JobKind.SPECTRUM, "k")
    clock.now += 10_000  # queued/running jobs never expire
    assert jobs.get(job.job_id) is not None

    jobs.run(job.job_id, lambda: RESULT)
    clock.now += 59
    assert jobs.get(job.job_id) is not None
    clock.now += 2
    assert jobs.get(job.job_id) is None
    assert expired == [job.job_id]
    _, created = jobs.find_or_create(JobKind.SPECTRUM, "k")
    assert created


def test_active_limit():
    jobs = make(max_active=2)
    jobs.find_or_create(JobKind.SPECTRUM, 1)
    jobs.find_or_create(JobKind.SPECTRUM, 2)
    jobs.find_or_create(JobKind.SPECTRUM, 1)  # existing job: still allowed
    with pytest.raises(TooManyJobs):
        jobs.find_or_create(JobKind.SPECTRUM, 3)


def test_only_max_running_jobs_run_at_once():
    jobs = make(max_running=1, max_active=10)
    running, peak, lock = 0, 0, threading.Lock()

    def work():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.02)
        with lock:
            running -= 1
        return RESULT

    ids = [jobs.find_or_create(JobKind.SPECTRUM, i)[0].job_id for i in range(5)]
    threads = [threading.Thread(target=jobs.run, args=(i, work)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1
    assert all(jobs.get(i).status is JobStatus.COMPLETED for i in ids)


def test_cleanup_errors_do_not_break_requests():
    clock = Clock()

    def bad_cleanup(_):
        raise OSError("disk")

    jobs = make(clock=clock, on_expire=bad_cleanup)
    job, _ = jobs.find_or_create(JobKind.SPECTRUM, "k")
    jobs.run(job.job_id, lambda: RESULT)
    clock.now += 120
    assert jobs.get(job.job_id) is None


def test_error_message_strips_html_pages():
    from app.services.jobs import error_message

    exc = RuntimeError("502 Proxy Error <html><head><title>502 Proxy Error</title></head>\n<body>busy</body></html>")
    assert error_message(exc) == "RuntimeError: 502 Proxy Error 502 Proxy Error busy"


def test_live_runner_maps_irsa_outage_to_readable_error(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services import live

    class DALServiceError(Exception):  # same name pyvo uses
        pass

    class Storage:
        def __init__(self, base_url, url_path):
            self.directory = tmp_path / url_path
            self.directory.mkdir(parents=True)

    def build_payload(*a, **k):
        raise DALServiceError("HTTP Code: 502 Proxy Error <html>...</html>")

    fake = SimpleNamespace(default_args=lambda **k: k, LocalStorage=Storage, build_payload=build_payload)
    monkeypatch.setattr(live, "_load_pipeline", lambda: fake)
    job_id = uuid4()
    with pytest.raises(live.UpstreamUnavailable, match=r"IRSA is unavailable or busy right now \(HTTP 502\)"):
        live.PipelineLiveRunner().sky_patch(live.coordinate_target(1, 2), 5.0, "http://x/", job_id)
    assert not (tmp_path / f"live/{job_id}").exists()  # partial outputs removed
