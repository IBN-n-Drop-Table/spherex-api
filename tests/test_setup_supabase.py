"""Offline tests for scripts/setup_supabase.py (fake Supabase client)."""

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_supabase.py"
spec = importlib.util.spec_from_file_location("setup_supabase", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

PROJECT = "https://abc.supabase.co"
BUCKET = "spherex-golden-path"
PUBLIC = f"{PROJECT}/storage/v1/object/public/{BUCKET}/golden_path"


class FakeBucketFiles:
    def __init__(self, store, bucket_id):
        self.store, self.id = store, bucket_id

    def upload(self, path, file, file_options):
        assert file_options["content-type"] == "image/png"
        assert file_options["upsert"] == "true"
        self.store.uploads[path] = file

    def get_public_url(self, path):
        return f"{PROJECT}/storage/v1/object/public/{self.id}/{path}?"


class FakeStorage:
    def __init__(self):
        self.buckets: dict[str, bool] = {}
        self.uploads: dict[str, bytes] = {}

    def list_buckets(self):
        return [SimpleNamespace(id=k, public=v) for k, v in self.buckets.items()]

    def create_bucket(self, bucket_id, options):
        self.buckets[bucket_id] = options["public"]

    def update_bucket(self, bucket_id, options):
        self.buckets[bucket_id] = options["public"]

    def from_(self, bucket_id):
        return FakeBucketFiles(self, bucket_id)


class FakeQuery:
    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self):
        self.storage = FakeStorage()

    def table(self, _):
        return FakeQuery()

    def rpc(self, *_):
        return FakeQuery()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data, pngs = tmp_path / "data", tmp_path / "png"
    data.mkdir()
    pngs.mkdir()
    shutil.copy(settings.data_dir / "wise_0855.json", data)
    for png in (settings.static_dir / "golden_path").glob("wise0855_*.png"):
        shutil.copy(png, pngs)
    monkeypatch.setattr(setup, "DATA_DIR", data)
    monkeypatch.setattr(setup, "PNG_DIR", pngs)
    monkeypatch.setattr(setup, "REPO_ROOT", tmp_path)
    for var in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_URL", "SUPABASE_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SUPABASE_URL", PROJECT)
    monkeypatch.setenv("SUPABASE_KEY", "sb_secret_test")
    client = FakeClient()
    monkeypatch.setattr(setup, "make_client", lambda cfg: client)
    # MIGRATION lives outside tmp REPO_ROOT; keep relative_to() happy.
    monkeypatch.setattr(setup, "MIGRATION", tmp_path / "migration.sql")
    (tmp_path / "migration.sql").write_text("select 1;")
    return SimpleNamespace(data=data, client=client, env=tmp_path / "none.env")


def test_publish_is_idempotent(workspace, capsys):
    json_file = workspace.data / "wise_0855.json"

    assert setup.main(["--env-file", str(workspace.env)]) == 0
    assert workspace.client.storage.buckets == {BUCKET: True}
    assert len(workspace.client.storage.uploads) == 7

    payload = json.loads(json_file.read_text())
    assert payload["difference_imaging"]["subtracted_image_url"] == f"{PUBLIC}/wise0855_diff.png"
    epoch = payload["timelapse_epochs"][0]
    assert epoch["image_url"] == f"{PUBLIC}/wise0855_epoch1_rgb.png"
    assert all(u.startswith(PUBLIC) for u in epoch["chemical_filters"].values())
    assert "READY" in capsys.readouterr().out

    first = json_file.read_text()
    assert setup.main(["--env-file", str(workspace.env)]) == 0
    assert json_file.read_text() == first
    assert "no changes needed" in capsys.readouterr().out


def test_bucket_url_is_rewritten_to_new_bucket(workspace):
    setup.main(["--env-file", str(workspace.env)])
    other = "https://abc.supabase.co/storage/v1/object/public/new-bucket/golden_path"
    patched, missing = setup.patch_payload(
        json.loads((workspace.data / "wise_0855.json").read_text()),
        {f"wise0855_{s}.png": f"{other}/wise0855_{s}.png" for s in
         ("diff", "epoch1_rgb", "epoch1_h2o", "epoch1_co2", "epoch2_rgb", "epoch2_h2o", "epoch2_co2")},
    )
    assert not missing
    assert patched["difference_imaging"]["subtracted_image_url"] == f"{other}/wise0855_diff.png"


def test_private_bucket_is_made_public(workspace):
    workspace.client.storage.buckets[BUCKET] = False
    setup.main(["--env-file", str(workspace.env)])
    assert workspace.client.storage.buckets[BUCKET] is True


def test_dry_run_touches_nothing(workspace, monkeypatch):
    monkeypatch.setattr(setup, "make_client", lambda cfg: pytest.fail("no client in dry run"))
    before = (workspace.data / "wise_0855.json").read_text()
    assert setup.main(["--dry-run", "--env-file", str(workspace.env)]) == 0
    assert (workspace.data / "wise_0855.json").read_text() == before


def test_missing_png_keeps_url_and_warns(workspace, capsys):
    (setup.PNG_DIR / "wise0855_diff.png").unlink()
    assert setup.main(["--env-file", str(workspace.env)]) == 0
    payload = json.loads((workspace.data / "wise_0855.json").read_text())
    assert payload["difference_imaging"]["subtracted_image_url"].startswith("http://localhost:8000")
    assert "wise0855_diff.png" in capsys.readouterr().out


def test_invalid_json_aborts_before_upload(workspace):
    (workspace.data / "broken.json").write_text('{"target_info": {}}')
    assert setup.main(["--env-file", str(workspace.env)]) == 1
    assert workspace.client.storage.uploads == {}


def test_missing_credentials(workspace, monkeypatch):
    monkeypatch.delenv("SUPABASE_KEY")
    assert setup.main(["--env-file", str(workspace.env)]) == 2


def test_foreign_urls_are_left_alone():
    missing: set[str] = set()
    url = "https://cdn.example.org/images/wise0855_diff.png"
    assert setup._rewrite(url, {"wise0855_diff.png": "x"}, missing) == url
    assert not missing


def test_env_parser(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nexport A=1\nB='two words'\nC=\"x#y\"\nD=val # trailing\nEMPTY=\nnoequals\n"
    )
    assert setup._parse_env_file(env) == {
        "A": "1", "B": "two words", "C": "x#y", "D": "val", "EMPTY": "",
    }


@pytest.mark.parametrize(
    ("key", "role"),
    [
        ("sb_secret_abc", "service_role"),
        ("sb_publishable_abc", "anon"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.sig", "anon"),
        ("garbage", "unknown"),
    ],
)
def test_key_role(key, role):
    assert setup.key_role(key) == role
