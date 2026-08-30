"""Local-only, Ollama-compatible structured model transport.

The adapter intentionally has no deterministic or remote fallback.  Callers
either receive one JSON object from the configured local model or a stable
``LocalModelError`` code that can be shown without exposing prompts or server
diagnostics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from signalforge.core.config import Settings, validate_local_model_base_url

LOCAL_MODEL_UNAVAILABLE = "LOCAL_MODEL_UNAVAILABLE"
LOCAL_MODEL_TIMEOUT = "LOCAL_MODEL_TIMEOUT"
LOCAL_MODEL_RESPONSE_ERROR = "LOCAL_MODEL_RESPONSE_ERROR"
LOCAL_MODEL_MODEL_MISMATCH = "LOCAL_MODEL_MODEL_MISMATCH"
LOCAL_MODEL_NOT_FOUND = "LOCAL_MODEL_NOT_FOUND"
LOCAL_MODEL_INVALID_URL = "LOCAL_MODEL_INVALID_URL"
INVALID_MODEL_JSON = "INVALID_MODEL_JSON"
_LOCAL_MODEL_ERROR_CODES = frozenset(
    {
        LOCAL_MODEL_UNAVAILABLE,
        LOCAL_MODEL_TIMEOUT,
        LOCAL_MODEL_RESPONSE_ERROR,
        LOCAL_MODEL_MODEL_MISMATCH,
        LOCAL_MODEL_NOT_FOUND,
        LOCAL_MODEL_INVALID_URL,
        INVALID_MODEL_JSON,
    }
)


@dataclass(frozen=True)
class LocalModelUsage:
    """Sanitized token and timing counters from one local generation."""

    prompt_eval_count: int | None
    eval_count: int | None
    total_duration_ns: int | None
    load_duration_ns: int | None
    prompt_eval_duration_ns: int | None
    eval_duration_ns: int | None


@dataclass(frozen=True)
class LocalModelGeneration:
    """One parsed object plus non-content generation metadata."""

    payload: Mapping[str, object] = field(repr=False)
    usage: LocalModelUsage
    response_model: str


@dataclass(frozen=True)
class LocalModelMetadata:
    """Allowlisted metadata for the exact configured local artifact."""

    tag: str
    digest: str
    size_bytes: int
    parameter_size: str | None
    quantization: str | None
    format: str | None
    family: str | None
    license_id: str | None
    ollama_version: str | None
    loaded_size_vram: int | None
    loaded_context_length: int | None


class LocalModelError(RuntimeError):
    """A safe model failure whose string form contains only its stable code."""

    def __init__(self, code: str) -> None:
        if code not in _LOCAL_MODEL_ERROR_CODES:
            code = LOCAL_MODEL_RESPONSE_ERROR
        self.code = code
        super().__init__(code)


def _non_negative_int(value: object) -> int | None:
    """Return a real non-negative integer, excluding booleans and coercion."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_metadata_string(value: object, *, max_length: int = 128) -> str | None:
    """Allow only compact single-line values in persisted metadata fields."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > max_length or not candidate.isprintable():
        return None
    if "\r" in candidate or "\n" in candidate:
        return None
    return candidate


def _required_metadata_string(value: object) -> str:
    candidate = _safe_metadata_string(value, max_length=256)
    if candidate is None:
        raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
    return candidate


def _normalize_license(value: object) -> str | None:
    """Map recognized local model license text to a compact SPDX identifier."""

    if not isinstance(value, str):
        return None
    normalized = " ".join(value.casefold().split())
    if "apache license version 2.0" in normalized:
        return "Apache-2.0"
    exact = value.strip()
    if exact in {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause"}:
        return exact
    return None


class LocalModelProvider:
    """Generate strict JSON through a configured Ollama-compatible service."""

    provider_name = "ollama"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport

    @property
    def model_name(self) -> str | None:
        """Return the configured model name, normalizing a blank value to absent."""

        value = self.settings.local_model_name
        return value.strip() if value and value.strip() else None

    @property
    def configured(self) -> bool:
        """Whether both endpoint and model name were explicitly configured."""

        base_url = self.settings.local_model_base_url
        return bool(base_url and base_url.strip() and self.model_name)

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        """Return one model-produced JSON object or raise a stable local error."""

        generation = await self.generate_json_with_metadata(
            system_prompt,
            user_prompt,
            response_schema=response_schema,
        )
        return generation.payload

    async def generate_json_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> LocalModelGeneration:
        """Generate one object and retain only allowlisted counters and model tag."""

        self._require_configuration()
        response = await self._request(
            "POST",
            "/api/chat",
            json_body={
                "model": self.model_name,
                "stream": False,
                "format": (
                    dict(response_schema) if response_schema is not None else "json"
                ),
                "think": self.settings.local_model_think,
                "options": {
                    "num_ctx": self.settings.local_model_context_length,
                    "temperature": self.settings.local_model_temperature,
                    "seed": self.settings.local_model_seed,
                    "num_predict": self.settings.local_model_max_output_tokens,
                },
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )

        envelope: dict[str, Any] | None = None
        payload: object = None
        invalid_json = False
        try:
            candidate_envelope = response.json()
            if not isinstance(candidate_envelope, dict):
                raise TypeError
            envelope = candidate_envelope
            message = envelope["message"]
            if not isinstance(message, dict):
                raise TypeError
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError
            payload = json.loads(content)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            invalid_json = True

        if invalid_json or envelope is None:
            raise LocalModelError(INVALID_MODEL_JSON)

        if not isinstance(payload, dict):
            raise LocalModelError(INVALID_MODEL_JSON)
        response_model = self._required_response_model(envelope.get("model"))
        return LocalModelGeneration(
            payload=payload,
            usage=LocalModelUsage(
                prompt_eval_count=_non_negative_int(envelope.get("prompt_eval_count")),
                eval_count=_non_negative_int(envelope.get("eval_count")),
                total_duration_ns=_non_negative_int(envelope.get("total_duration")),
                load_duration_ns=_non_negative_int(envelope.get("load_duration")),
                prompt_eval_duration_ns=_non_negative_int(
                    envelope.get("prompt_eval_duration")
                ),
                eval_duration_ns=_non_negative_int(envelope.get("eval_duration")),
            ),
            response_model=response_model,
        )

    async def model_metadata(self) -> LocalModelMetadata:
        """Read allowlisted metadata for the exact configured local model tag."""

        self._require_configuration()
        tags_payload = self._required_json_object(
            await self._request("GET", "/api/tags")
        )
        tag, tag_item = self._select_required_tag(tags_payload)

        version_payload = self._required_json_object(
            await self._request("GET", "/api/version")
        )
        version = _safe_metadata_string(version_payload.get("version"))
        if version is None:
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)

        show_payload = self._required_json_object(
            await self._request("POST", "/api/show", json_body={"model": tag})
        )
        tag_details = tag_item.get("details")
        show_details = show_payload.get("details")
        if tag_details is not None and not isinstance(tag_details, dict):
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        if show_details is not None and not isinstance(show_details, dict):
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        details = {
            **(show_details if isinstance(show_details, dict) else {}),
            **(tag_details if isinstance(tag_details, dict) else {}),
        }

        loaded_size_vram: int | None = None
        loaded_context_length: int | None = None
        try:
            ps_response = await self._request("GET", "/api/ps")
        except LocalModelError:
            pass
        else:
            loaded_item = self._select_optional_loaded_model(ps_response, tag)
            if loaded_item is not None:
                loaded_size_vram = _non_negative_int(loaded_item.get("size_vram"))
                loaded_context_length = _non_negative_int(
                    loaded_item.get("context_length")
                )

        digest = _required_metadata_string(tag_item.get("digest"))
        size_bytes = _non_negative_int(tag_item.get("size"))
        if size_bytes is None:
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        return LocalModelMetadata(
            tag=tag,
            digest=digest,
            size_bytes=size_bytes,
            parameter_size=_safe_metadata_string(details.get("parameter_size")),
            quantization=_safe_metadata_string(details.get("quantization_level")),
            format=_safe_metadata_string(details.get("format")),
            family=_safe_metadata_string(details.get("family")),
            license_id=_normalize_license(show_payload.get("license")),
            ollama_version=version,
            loaded_size_vram=loaded_size_vram,
            loaded_context_length=loaded_context_length,
        )

    async def health(self) -> bool:
        """Verify the local service and configured model are both ready."""

        self._require_configuration()
        response = await self._request("GET", "/api/tags")
        payload: dict[str, Any] | None = None
        malformed_response = False
        try:
            candidate_payload = response.json()
            if not isinstance(candidate_payload, dict) or not isinstance(
                candidate_payload.get("models"), list
            ):
                raise TypeError
            payload = candidate_payload
            names = {
                name
                for item in payload["models"]
                if isinstance(item, dict)
                for name in (item.get("name"), item.get("model"))
                if isinstance(name, str)
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            malformed_response = True

        if malformed_response or payload is None:
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)

        expected = self.model_name
        assert expected is not None  # guaranteed by _require_configuration
        accepted_names = {expected}
        if ":" not in expected:
            accepted_names.add(f"{expected}:latest")
        if names.isdisjoint(accepted_names):
            raise LocalModelError(LOCAL_MODEL_NOT_FOUND)
        return True

    def _accepted_model_names(self) -> tuple[str, ...]:
        expected = self.model_name
        assert expected is not None  # guaranteed by callers after configuration
        if ":" in expected:
            return (expected,)
        return (expected, f"{expected}:latest")

    def _required_response_model(self, value: object) -> str:
        expected = self.model_name
        assert expected is not None  # guaranteed by _require_configuration
        if not isinstance(value, str) or value != expected:
            raise LocalModelError(LOCAL_MODEL_MODEL_MISMATCH)
        return value

    def _required_json_object(self, response: httpx.Response) -> dict[str, Any]:
        payload: object = None
        malformed_response = False
        try:
            payload = response.json()
        except (json.JSONDecodeError, TypeError, ValueError):
            malformed_response = True
        if malformed_response:
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        if not isinstance(payload, dict):
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        return payload

    def _select_required_tag(
        self, payload: Mapping[str, object]
    ) -> tuple[str, dict[str, Any]]:
        models = payload.get("models")
        if not isinstance(models, list):
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        for accepted_name in self._accepted_model_names():
            for item in models:
                if not isinstance(item, dict):
                    continue
                names = (item.get("name"), item.get("model"))
                if accepted_name in names:
                    return accepted_name, item
        raise LocalModelError(LOCAL_MODEL_NOT_FOUND)

    def _select_optional_loaded_model(
        self, response: httpx.Response, selected_tag: str
    ) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return None
        accepted_names = {selected_tag, *self._accepted_model_names()}
        for item in payload["models"]:
            if not isinstance(item, dict):
                continue
            if item.get("name") in accepted_names or item.get("model") in accepted_names:
                return item
        return None

    def _require_configuration(self) -> None:
        if not self.configured:
            raise LocalModelError(LOCAL_MODEL_UNAVAILABLE)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        invalid_url = False
        try:
            base_url = validate_local_model_base_url(self.settings.local_model_base_url)
        except (httpx.InvalidURL, TypeError, ValueError):
            invalid_url = True
            base_url = None
        if invalid_url:
            raise LocalModelError(LOCAL_MODEL_INVALID_URL)
        if base_url is None:
            raise LocalModelError(LOCAL_MODEL_UNAVAILABLE)
        url = f"{base_url.rstrip('/')}{path}"
        failure_code: str | None = None
        response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self.settings.local_model_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.request(method, url, json=json_body)
        except httpx.InvalidURL:
            failure_code = LOCAL_MODEL_INVALID_URL
        except httpx.TimeoutException:
            failure_code = LOCAL_MODEL_TIMEOUT
        except httpx.RequestError:
            failure_code = LOCAL_MODEL_UNAVAILABLE

        if failure_code is not None:
            raise LocalModelError(failure_code)
        assert response is not None

        if (
            path == "/api/chat"
            and response.status_code == 404
            and self._is_explicit_model_not_found(response)
        ):
            raise LocalModelError(LOCAL_MODEL_NOT_FOUND)
        if not 200 <= response.status_code < 300:
            raise LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)
        return response

    def _is_explicit_model_not_found(self, response: httpx.Response) -> bool:
        """Recognize only Ollama's named-model 404, not generic/authentication 404s."""

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return False
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), str):
            return False
        error = " ".join(payload["error"].lower().split())
        expected = self.model_name
        return bool(
            expected
            and error.startswith("model ")
            and expected.lower() in error
            and " not found" in error
        )
