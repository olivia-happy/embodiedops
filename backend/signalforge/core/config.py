"""Runtime configuration loaded from environment variables only."""

from __future__ import annotations

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_MODEL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
)


def validate_local_model_base_url(value: object) -> str | None:
    """Return a canonical approved local URL or reject it without echoing secrets."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("local_model_base_url must be a local HTTP(S) URL")
    candidate = value.strip()
    if not candidate:
        return None
    if "?" in candidate or "#" in candidate:
        raise ValueError("local_model_base_url cannot contain query or fragment data")

    try:
        parsed = httpx.URL(candidate)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise ValueError("local_model_base_url is invalid") from exc

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("local_model_base_url must use HTTP(S)")
    if parsed.userinfo:
        raise ValueError("local_model_base_url cannot contain user information")
    if parsed.host not in LOCAL_MODEL_HOSTS:
        raise ValueError("local_model_base_url must target an approved local host")

    # Inspect the original authority/path as well as the normalized URL.  This
    # rejects encoded or dot-segment paths that a URL parser might normalize to
    # the root, and rejects an explicit empty port rather than silently dropping it.
    remainder = candidate.split("://", maxsplit=1)[1]
    authority, separator, path_tail = remainder.partition("/")
    raw_path = f"/{path_tail}" if separator else ""
    if raw_path not in {"", "/"}:
        raise ValueError("local_model_base_url path must be empty or root")
    if "@" in authority or authority.endswith(":"):
        raise ValueError("local_model_base_url authority is invalid")

    if authority.startswith("["):
        closing_bracket = authority.find("]")
        raw_host = authority[1:closing_bracket] if closing_bracket >= 0 else ""
    else:
        raw_host = authority.rsplit(":", maxsplit=1)[0] if ":" in authority else authority
    if raw_host.lower() not in LOCAL_MODEL_HOSTS:
        raise ValueError("local_model_base_url must use an explicit local host")

    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("local_model_base_url port is invalid")
    return str(parsed)


class Settings(BaseSettings):
    """Configuration for local development and optional LLM enhancement.

    No LLM credential is required for the deterministic application path.  The
    values remain nullable so a missing key cannot prevent a local demo from
    starting.
    """

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    database_path: str = "../data/signalforge.duckdb"
    local_model_base_url: str | None = None
    local_model_name: str | None = None
    local_model_timeout_seconds: float = Field(default=120.0, gt=0)
    local_model_context_length: int = Field(default=16384, ge=4096, le=262144)
    local_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    local_model_seed: int = Field(default=42, ge=0)
    local_model_max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    local_model_think: bool = True
    demo_stale_after_days: int = 30
    demo_read_only: bool = False

    @field_validator("local_model_base_url", mode="before")
    @classmethod
    def local_model_url_is_local(cls, value: object) -> str | None:
        """Prevent evidence-bearing model requests from targeting remote hosts."""

        return validate_local_model_base_url(value)

    @classmethod
    def from_env(cls) -> Settings:
        """Create settings from the current process environment and `.env`."""

        return cls()
