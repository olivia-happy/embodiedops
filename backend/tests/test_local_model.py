"""Tests for the local-only Ollama-compatible model adapter."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from signalforge.core.config import Settings
from signalforge.services.local_model import (
    LOCAL_MODEL_MODEL_MISMATCH,
    LOCAL_MODEL_RESPONSE_ERROR,
    LocalModelError,
    LocalModelProvider,
)


def _assert_exception_is_sanitized(error: BaseException, sentinel: str) -> None:
    """Reject sensitive text anywhere in a recursively reachable exception chain."""

    pending = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(vars(current))))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)

    assert len(seen) == 1
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sentinel not in "\n".join(rendered)


@pytest.fixture
def anyio_backend() -> str:
    """Run async contract tests on the asyncio backend used by FastAPI."""

    return "asyncio"


def configured_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "database_path": ":memory:",
        "local_model_base_url": "http://localhost:11434",
        "local_model_name": "qwen-local:latest",
        "local_model_timeout_seconds": 2,
    }
    values.update(updates)
    return Settings(**values)


def chat_transport(
    content: str,
    *,
    response_schema: dict[str, object] | None = None,
    envelope_updates: dict[str, Any] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/chat"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "model": "qwen-local:latest",
            "stream": False,
            "format": response_schema if response_schema is not None else "json",
            "think": True,
            "options": {
                "num_ctx": 16384,
                "temperature": 0.0,
                "seed": 42,
                "num_predict": 2048,
            },
            "messages": [
                {"role": "system", "content": "只返回 JSON。"},
                {"role": "user", "content": "拆分服务问题。"},
            ],
        }
        envelope: dict[str, Any] = {
            "model": "qwen-local:latest",
            "message": {"content": content},
        }
        envelope.update(envelope_updates or {})
        return httpx.Response(200, json=envelope)

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_provider_generates_one_json_object() -> None:
    provider = LocalModelProvider(
        configured_settings(), transport=chat_transport('{"subproblems": []}')
    )

    result = await provider.generate_json("只返回 JSON。", "拆分服务问题。")

    assert result == {"subproblems": []}


@pytest.mark.anyio
async def test_provider_passes_the_exact_response_schema_to_ollama() -> None:
    response_schema: dict[str, object] = {
        "title": "GroupingDraft",
        "type": "object",
        "properties": {"subproblems": {"type": "array"}},
    }
    provider = LocalModelProvider(
        configured_settings(),
        transport=chat_transport(
            '{"subproblems": []}', response_schema=response_schema
        ),
    )

    result = await provider.generate_json(
        "只返回 JSON。",
        "拆分服务问题。",
        response_schema=response_schema,
    )

    assert result == {"subproblems": []}


@pytest.mark.anyio
async def test_generation_exposes_only_sanitized_usage_and_model_tag() -> None:
    provider = LocalModelProvider(
        configured_settings(),
        transport=chat_transport(
            '{"subproblems": []}',
            envelope_updates={
                "model": "qwen-local:latest",
                "prompt_eval_count": 31,
                "eval_count": 17,
                "total_duration": 1000,
                "load_duration": 200,
                "prompt_eval_duration": 300,
                "eval_duration": 500,
                "thinking": "private chain of thought",
            },
        ),
    )

    generation = await provider.generate_json_with_metadata(
        "只返回 JSON。", "拆分服务问题。"
    )

    assert generation.payload == {"subproblems": []}
    assert asdict(generation.usage) == {
        "prompt_eval_count": 31,
        "eval_count": 17,
        "total_duration_ns": 1000,
        "load_duration_ns": 200,
        "prompt_eval_duration_ns": 300,
        "eval_duration_ns": 500,
    }
    assert generation.response_model == "qwen-local:latest"
    assert "thinking" not in repr(generation)
    assert "private chain" not in repr(generation)


@pytest.mark.anyio
async def test_usage_rejects_negative_boolean_and_non_integer_values() -> None:
    provider = LocalModelProvider(
        configured_settings(),
        transport=chat_transport(
            "{}",
            envelope_updates={
                "prompt_eval_count": -1,
                "eval_count": True,
                "total_duration": 1.5,
                "load_duration": "2",
            },
        ),
    )

    generation = await provider.generate_json_with_metadata(
        "只返回 JSON。", "拆分服务问题。"
    )

    assert asdict(generation.usage) == {
        "prompt_eval_count": None,
        "eval_count": None,
        "total_duration_ns": None,
        "load_duration_ns": None,
        "prompt_eval_duration_ns": None,
        "eval_duration_ns": None,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "envelope",
    [
        {"message": {"content": "{}"}},
        {
            "model": "WRONG-MODEL-SENSITIVE-TAG",
            "message": {"content": "{}"},
        },
    ],
)
async def test_generation_fails_closed_when_response_model_is_missing_or_wrong(
    envelope: dict[str, object],
) -> None:
    provider = LocalModelProvider(
        configured_settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope)),
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("PRIVATE-SYSTEM-PROMPT", "PRIVATE-EVIDENCE-PROMPT")

    assert captured.value.code == LOCAL_MODEL_MODEL_MISMATCH
    _assert_exception_is_sanitized(captured.value, "WRONG-MODEL-SENSITIVE-TAG")


@pytest.mark.anyio
async def test_generation_requires_exact_configured_tag_not_latest_alias() -> None:
    provider = LocalModelProvider(
        configured_settings(local_model_name="qwen-local"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "model": "qwen-local:latest",
                    "message": {"content": "{}"},
                },
            )
        ),
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("PRIVATE-SYSTEM-PROMPT", "PRIVATE-EVIDENCE-PROMPT")

    assert captured.value.code == LOCAL_MODEL_MODEL_MISMATCH
    _assert_exception_is_sanitized(captured.value, "PRIVATE-EVIDENCE-PROMPT")


@pytest.mark.anyio
async def test_model_metadata_is_pinned_and_privacy_safe() -> None:
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen-local:latest",
                            "digest": "sha256:abc123",
                            "size": 6_600_000_000,
                            "details": {
                                "format": "gguf",
                                "family": "qwen3",
                                "parameter_size": "9B",
                                "quantization_level": "Q4_K_M",
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.6"})
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "license": "Apache License Version 2.0\nprivate long text",
                    "details": {"format": "gguf", "family": "qwen3"},
                    "template": "private prompt template",
                },
            )
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen-local:latest",
                            "size_vram": 5_500_000_000,
                            "context_length": 16384,
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    provider = LocalModelProvider(
        configured_settings(), transport=httpx.MockTransport(handler)
    )

    metadata = await provider.model_metadata()

    assert asdict(metadata) == {
        "tag": "qwen-local:latest",
        "digest": "sha256:abc123",
        "size_bytes": 6_600_000_000,
        "parameter_size": "9B",
        "quantization": "Q4_K_M",
        "format": "gguf",
        "family": "qwen3",
        "license_id": "Apache-2.0",
        "ollama_version": "0.32.6",
        "loaded_size_vram": 5_500_000_000,
        "loaded_context_length": 16384,
    }
    assert requests == [
        ("GET", "/api/tags", None),
        ("GET", "/api/version", None),
        ("POST", "/api/show", {"model": "qwen-local:latest"}),
        ("GET", "/api/ps", None),
    ]
    assert "private" not in repr(metadata)


@pytest.mark.anyio
async def test_model_metadata_accepts_latest_alias_and_optional_ps_absence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen-local:latest",
                            "digest": "sha256:abc123",
                            "size": 123,
                            "details": {},
                        }
                    ]
                },
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.6"})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {}})
        if request.url.path == "/api/ps":
            return httpx.Response(404, json={"error": "route not found"})
        raise AssertionError

    provider = LocalModelProvider(
        configured_settings(local_model_name="qwen-local"),
        transport=httpx.MockTransport(handler),
    )

    metadata = await provider.model_metadata()

    assert metadata.tag == "qwen-local:latest"
    assert metadata.loaded_size_vram is None
    assert metadata.loaded_context_length is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tags_payload",
    [
        {},
        {"models": "not-a-list"},
        {"models": [{"name": "qwen-local:latest", "digest": "", "size": 1}]},
        {"models": [{"name": "qwen-local:latest", "digest": "abc", "size": -1}]},
    ],
)
async def test_model_metadata_rejects_malformed_required_tag_data(
    tags_payload: dict[str, object],
) -> None:
    provider = LocalModelProvider(
        configured_settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=tags_payload)
        ),
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.model_metadata()

    assert captured.value.code == "LOCAL_MODEL_RESPONSE_ERROR"


@pytest.mark.anyio
async def test_malformed_metadata_body_does_not_survive_in_an_exception_cause() -> None:
    provider = LocalModelProvider(
        configured_settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text='{"private":"unfinished')
        ),
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.model_metadata()

    assert captured.value.code == "LOCAL_MODEL_RESPONSE_ERROR"
    _assert_exception_is_sanitized(captured.value, "private")


@pytest.mark.anyio
async def test_provider_rejects_non_object_json() -> None:
    provider = LocalModelProvider(
        configured_settings(), transport=chat_transport('["not-object"]')
    )

    with pytest.raises(LocalModelError, match="INVALID_MODEL_JSON") as captured:
        await provider.generate_json("只返回 JSON。", "拆分服务问题。")

    assert captured.value.code == "INVALID_MODEL_JSON"


@pytest.mark.anyio
async def test_invalid_model_json_does_not_survive_in_an_exception_cause() -> None:
    provider = LocalModelProvider(
        configured_settings(), transport=chat_transport('{"private":"unfinished')
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("只返回 JSON。", "拆分服务问题。")

    assert captured.value.code == "INVALID_MODEL_JSON"
    _assert_exception_is_sanitized(captured.value, "private")


@pytest.mark.anyio
async def test_provider_maps_invalid_envelope_to_stable_error() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"response": "{}"}))
    provider = LocalModelProvider(configured_settings(), transport=transport)

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("system", "user")

    assert captured.value.code == "INVALID_MODEL_JSON"


@pytest.mark.anyio
async def test_non_model_response_failure_is_not_misclassified_as_mismatch() -> None:
    provider = LocalModelProvider(
        configured_settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(500, text="PRIVATE-SERVER-DIAGNOSTIC")
        ),
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("PRIVATE-SYSTEM", "PRIVATE-EVIDENCE")

    assert captured.value.code == LOCAL_MODEL_RESPONSE_ERROR
    assert captured.value.code != LOCAL_MODEL_MODEL_MISMATCH
    _assert_exception_is_sanitized(captured.value, "PRIVATE-SERVER-DIAGNOSTIC")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (httpx.ReadTimeout("slow model"), "LOCAL_MODEL_TIMEOUT"),
        (httpx.ConnectError("model is stopped"), "LOCAL_MODEL_UNAVAILABLE"),
    ],
)
async def test_provider_maps_transport_failures_to_stable_errors(
    exception: httpx.RequestError, expected_code: str
) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise exception

    provider = LocalModelProvider(configured_settings(), transport=httpx.MockTransport(fail))

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("system", "user")

    assert captured.value.code == expected_code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exception_type", "expected_code"),
    [
        (httpx.ReadTimeout, "LOCAL_MODEL_TIMEOUT"),
        (httpx.ConnectError, "LOCAL_MODEL_UNAVAILABLE"),
    ],
)
async def test_transport_failure_chain_discards_request_and_prompt_data(
    exception_type: type[httpx.RequestError], expected_code: str
) -> None:
    sentinel = "SENSITIVE-REQUEST-BODY-IN-HTTPX-ERROR"

    def fail(request: httpx.Request) -> httpx.Response:
        raise exception_type(sentinel, request=request)

    provider = LocalModelProvider(
        configured_settings(), transport=httpx.MockTransport(fail)
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json(sentinel, sentinel)

    assert captured.value.code == expected_code
    _assert_exception_is_sanitized(captured.value, sentinel)


@pytest.mark.anyio
async def test_provider_maps_httpx_invalid_url_to_stable_error() -> None:
    sentinel = "SENSITIVE-INVALID-URL-DETAIL"

    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL(sentinel)

    provider = LocalModelProvider(configured_settings(), transport=httpx.MockTransport(fail))

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("system", "user")

    assert captured.value.code == "LOCAL_MODEL_INVALID_URL"
    _assert_exception_is_sanitized(captured.value, sentinel)


@pytest.mark.anyio
async def test_provider_defensively_rejects_mutated_remote_url_before_transport() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {"content": "{}"}})

    settings = configured_settings()
    settings.local_model_base_url = "https://api.openai.com"
    provider = LocalModelProvider(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("private evidence", "private prompt")

    assert captured.value.code == "LOCAL_MODEL_INVALID_URL"
    assert calls == 0
    _assert_exception_is_sanitized(captured.value, "private prompt")


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [302, 401, 500])
async def test_provider_maps_http_failure_without_leaking_response_body(
    status_code: int,
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status_code, text="secret model server diagnostics")
    )
    provider = LocalModelProvider(configured_settings(), transport=transport)

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("system", "user")

    assert captured.value.code == "LOCAL_MODEL_RESPONSE_ERROR"
    assert "secret" not in str(captured.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"error": "model 'qwen-local:latest' not found"}, "LOCAL_MODEL_NOT_FOUND"),
        ({"error": "not found"}, "LOCAL_MODEL_RESPONSE_ERROR"),
        ({"error": "authentication route not found"}, "LOCAL_MODEL_RESPONSE_ERROR"),
    ],
)
async def test_chat_404_only_maps_explicit_missing_model(
    payload: dict[str, str], expected_code: str
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(404, json=payload))
    provider = LocalModelProvider(configured_settings(), transport=transport)

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("system", "user")

    assert captured.value.code == expected_code


@pytest.mark.anyio
async def test_health_404_is_not_misclassified_as_missing_configured_model() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(404, json={"error": "model 'qwen-local:latest' not found"})
    )
    provider = LocalModelProvider(configured_settings(), transport=transport)

    with pytest.raises(LocalModelError) as captured:
        await provider.health()

    assert captured.value.code == "LOCAL_MODEL_RESPONSE_ERROR"


@pytest.mark.anyio
async def test_unconfigured_provider_has_no_fallback_and_makes_no_request() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {"content": "{}"}})

    provider = LocalModelProvider(
        configured_settings(local_model_name=None), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(LocalModelError) as captured:
        await provider.generate_json("system", "user")

    assert captured.value.code == "LOCAL_MODEL_UNAVAILABLE"
    assert calls == 0


@pytest.mark.anyio
async def test_health_requires_the_configured_model_to_be_installed() -> None:
    available = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"models": [{"name": "qwen-local:latest"}]},
            request=request,
        )
    )
    missing = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"models": [{"name": "another-model:latest"}]},
            request=request,
        )
    )

    assert await LocalModelProvider(configured_settings(), transport=available).health() is True
    with pytest.raises(LocalModelError) as captured:
        await LocalModelProvider(configured_settings(), transport=missing).health()
    assert captured.value.code == "LOCAL_MODEL_NOT_FOUND"


def test_local_model_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        configured_settings(local_model_timeout_seconds=0)
