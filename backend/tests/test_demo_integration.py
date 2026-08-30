from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_verify_local_exposes_an_opt_in_read_only_gate() -> None:
    script = (ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")

    assert "ExpectedDemoReadOnly" in script
    assert "DEMO_READ_ONLY" in script
    assert "StatusCode -ne 403" in script


def test_compose_routes_browser_api_same_origin_and_wires_read_only_mode() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "SIGNALFORGE_API_ORIGIN: ${SIGNALFORGE_API_ORIGIN:-http://api:8000}" in compose
    assert "NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:-}" in compose
    assert "DEMO_READ_ONLY: ${DEMO_READ_ONLY:-false}" in compose
    assert "NEXT_PUBLIC_DEMO_READ_ONLY: ${NEXT_PUBLIC_DEMO_READ_ONLY:-false}" in compose
    assert '"${WEB_HOST_PORT:-3000}:3000"' in compose
