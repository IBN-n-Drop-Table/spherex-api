import os

# Tests must never touch a real Supabase project, even if the developer's
# shell exports these. Runs before app.core.config is imported.
for var in ("SUPABASE_URL", "SUPABASE_KEY"):
    os.environ.pop(var, None)
os.environ["LIVE_WARMUP"] = "0"  # don't import the science stack in a background thread
for var in ("PUBLIC_BASE",):
    os.environ.pop(var, None)

import json  # noqa: E402

import pytest  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.urls import to_internal, to_public  # noqa: E402
from app.schemas.sky_patch import SkyPatchResponse  # noqa: E402
from app.schemas.spectrum import SpectrumResponse  # noqa: E402

TEST_BASE = "http://testserver"


def load_raw(name: str = "wise_0855.json") -> dict:
    """The file as stored on disk (image paths relative to the API)."""
    return json.loads((settings.data_dir / name).read_text("utf-8"))


def load_patch(name: str = "wise_0855.json") -> dict:
    """What the API serves for a cached file: normalised by the model, with the
    serve-time annotations (app/services/annotations.py), absolute URLs."""
    from app.services.annotations import annotate

    return to_public(annotate(SkyPatchResponse.model_validate(to_internal(load_raw(name)))), TEST_BASE)


class FakeLiveRunner:
    """Stands in for the IRSA pipeline: instant, deterministic, no network."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple] = []

    epochs_result: list[dict] | Exception = []

    def _patch(self, target, fov, job_id, progress) -> SkyPatchResponse:
        self.calls.append((target.key, target.ra_deg, target.dec_deg, fov, job_id))
        if progress:
            progress("download", 0.5, "Descargando recortes de IRSA: 1/2")
        if self.fail:
            raise self.fail
        raw = to_internal(load_raw())
        raw["target_info"].update(name=target.name, ra_deg=target.ra_deg, dec_deg=target.dec_deg,
                                  is_cached_golden_path=False)
        return SkyPatchResponse.model_validate(raw)

    def sky_patch(self, target, fov, job_id, progress=None):
        return self._patch(target, fov, job_id, progress)

    def epochs(self, ra, dec):
        if isinstance(self.epochs_result, Exception):
            raise self.epochs_result
        return self.epochs_result

    def spectrum(self, target, fov, job_id, progress=None):
        patch = self._patch(target, fov, job_id, progress)
        return SpectrumResponse(
            source="target", target_name=target.name, ra_deg=target.ra_deg, dec_deg=target.dec_deg,
            epoch_id=patch.timelapse_epochs[0].epoch_id, is_cached_golden_path=False,
            spectrum_102_bands=patch.spectrum_102_bands,
        )


@pytest.fixture
def live_client():
    """TestClient with a fresh job store and a fake live runner.

    TestClient runs BackgroundTasks before returning, so a 202 job is
    already finished by the time the test polls it."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.jobs import JobService, get_job_service
    from app.services.live import get_live_runner

    runner = FakeLiveRunner()
    jobs = JobService(ttl_s=3600, max_running=1, max_active=8)
    app.dependency_overrides[get_live_runner] = lambda: runner
    app.dependency_overrides[get_job_service] = lambda: jobs
    with TestClient(app) as client:
        client.runner, client.jobs = runner, jobs
        yield client
    app.dependency_overrides.clear()
