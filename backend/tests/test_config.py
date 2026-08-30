"""Tests for environment-safe settings loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from signalforge.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_local_model_generation_defaults_are_reproducible() -> None:
    settings = Settings(_env_file=None)

    assert settings.local_model_timeout_seconds == 120.0
    assert settings.local_model_context_length == 16384
    assert settings.local_model_temperature == 0.0
    assert settings.local_model_seed == 42
    assert settings.local_model_max_output_tokens == 2048
    assert settings.local_model_think is True


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("local_model_context_length", 4095),
        ("local_model_temperature", 2.01),
        ("local_model_seed", -1),
        ("local_model_max_output_tokens", 255),
    ],
)
def test_local_model_generation_settings_reject_invalid_boundaries(
    field_name: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_delivery_configuration_is_pinned_to_the_local_model_only() -> None:
    env_text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    compose_text = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    expected = {
        "LOCAL_MODEL_BASE_URL": "http://host.docker.internal:11434",
        "LOCAL_MODEL_NAME": "qwen3.5:9b",
        "LOCAL_MODEL_TIMEOUT_SECONDS": "120",
        "LOCAL_MODEL_CONTEXT_LENGTH": "16384",
        "LOCAL_MODEL_TEMPERATURE": "0",
        "LOCAL_MODEL_SEED": "42",
        "LOCAL_MODEL_MAX_OUTPUT_TOKENS": "2048",
        "LOCAL_MODEL_THINK": "true",
    }

    for name, value in expected.items():
        assert f"{name}={value}" in env_text
        assert name in compose_text
        assert value in compose_text
    for remote_name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        assert remote_name not in env_text
        assert remote_name not in compose_text
    assert '"host.docker.internal:host-gateway"' in compose_text


def test_legacy_remote_llm_environment_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", "test-data/demo.duckdb")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen-local")
    monkeypatch.setenv("LLM_API_KEY", "SHOULD-NOT-ENTER-SETTINGS")

    settings = Settings.from_env()

    assert settings.database_path == "test-data/demo.duckdb"
    assert not hasattr(settings, "llm_base_url")
    assert not hasattr(settings, "llm_model")
    assert not hasattr(settings, "llm_api_key")
    assert not any(name.startswith("llm_") for name in settings.model_dump())


def test_blank_environment_values_fall_back_to_safe_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", "")
    monkeypatch.setenv("LLM_API_KEY", "")

    settings = Settings.from_env()

    assert settings.database_path == "../data/signalforge.duckdb"
    assert not hasattr(settings, "llm_api_key")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com",
        "http://192.168.1.20:11434",
        "http://127.0.0.2:11434",
        "http://ollama:11434",
        "http://localhost.evil.example:11434",
        "ftp://localhost:11434",
        "http://user:secret@localhost:11434",
        "http://localhost:11434?token=secret",
        "http://localhost:11434#fragment",
        "http://localhost:11434/api",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:11434//",
        "http://[::1",
        "not-a-url",
    ],
)
def test_local_model_base_url_rejects_remote_or_ambiguous_targets(base_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(local_model_base_url=base_url, local_model_name="qwen-local:latest")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434",
        "https://localhost/",
        "http://127.0.0.1:11434/",
        "http://[::1]:11434",
        "http://host.docker.internal:11434",
    ],
)
def test_local_model_base_url_accepts_only_explicit_local_targets(base_url: str) -> None:
    settings = Settings(local_model_base_url=base_url, local_model_name="qwen-local:latest")

    assert settings.local_model_base_url
