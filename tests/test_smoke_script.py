"""scripts/smoke_test.py helpers (the full script is run against a server by hand)."""

import importlib.util
import socket
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test.py"
spec = importlib.util.spec_from_file_location("smoke_test", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]  # closed again on exit: nothing listens there


def test_network_errors_do_not_crash():
    status, headers, body, _ = smoke.request(f"http://127.0.0.1:{_closed_port()}/health", timeout=5)
    assert status == 0 and headers == {} and body.startswith(b"network error")


def test_image_urls_include_pairs_and_filters():
    patch = {"timelapse_epochs": [{"image_url": "a", "chemical_filters": {"H2O": "b"}}],
             "difference_imaging": {"subtracted_image_url": "c", "pairwise": [{"subtracted_image_url": "d"}]}}
    assert smoke.image_urls(patch) == ["a", "b", "c", "d"]


def test_url_host_check_catches_the_wrong_domain_or_scheme(capsys):
    r = smoke.Report()
    smoke.check_urls(r, "ok", ["https://api.example/static/x.png"], "https://api.example")
    smoke.check_urls(r, "http", ["http://api.example/static/x.png"], "https://api.example")
    smoke.check_urls(r, "host", ["https://other.example/static/x.png"], "https://api.example")
    assert (r.passed, r.failed) == (1, 2)
    assert capsys.readouterr().out.count("FALLO") == 2
