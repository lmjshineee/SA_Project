"""Offline protocol tests only; these never prove live-model availability."""

import json

import httpx
import pytest
from pydantic import ValidationError

from office_agents.config import Settings, load_settings
from office_agents.models.chat import ChatModel, ModelError
from office_agents.verify import run_verification


TEST_KEY = "synthetic-secret-for-offline-tests"


def settings(**kwargs):
    return Settings(api_key=TEST_KEY, **kwargs)


def response(message=None, *, reason="stop", usage=None):
    return {"model": "fixture-model", "choices": [{
        "finish_reason": reason,
        "message": message or {"role": "assistant", "content": "M1_TEXT_OK"},
    }], "usage": usage}


def client(handler):
    return ChatModel(settings(), "test-session", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url", [
    "https://user:password@example.com/v1", "https://example.com/v1?key=private",
    "https://example.com/v1#private", "http://example.com/v1", "file:///tmp/model", "https:///missing",
    "https://example.com:99999/v1",
])
def test_configuration_rejects_credential_urls_and_insecure_remote(url):
    with pytest.raises(ValidationError):
        settings(base_url=url)


def test_config_secret_hidden_and_env_does_not_override_shell(tmp_path, monkeypatch):
    for name in ("MODEL_PROVIDER", "MODEL_NAME", "MODEL_BASE_URL", "MODEL_TEMPERATURE",
                 "MODEL_MAX_OUTPUT_TOKENS", "MODEL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_API_KEY", TEST_KEY)
    monkeypatch.setenv("MODEL_NAME", "shell-model")
    config = tmp_path / ".env"
    config.write_text("MODEL_NAME=file-model\nMODEL_API_KEY=file-key\n")
    loaded = load_settings(config)
    assert loaded.model == "shell-model"
    assert TEST_KEY not in repr(loaded)
    assert "api_key" not in loaded.public_config()


@pytest.mark.parametrize("key", ["", "   "])
def test_empty_key_is_rejected(key):
    with pytest.raises(ValidationError):
        Settings(api_key=key)


def test_request_endpoint_session_and_usage():
    def handler(request):
        assert str(request.url) == "https://opencode.ai/inference/go/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + TEST_KEY
        assert request.headers["x-opencode-session"] == "test-session"
        assert request.headers["user-agent"] == "SA-Project-M1/0.1"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4.1-flash"
        assert payload["max_tokens"] == 4096
        return httpx.Response(200, json=response(usage={"total_tokens": 10}))
    model = client(handler)
    try:
        assert model.complete([]).usage == {"total_tokens": 10}
    finally:
        model.close()


def test_optional_null_tools_are_accepted():
    model = client(lambda request: httpx.Response(200, json=response({
        "role": "assistant", "content": "M1_TEXT_OK", "tool_calls": None,
    })))
    try:
        assert model.complete([]).message.tool_calls == []
    finally:
        model.close()


@pytest.mark.parametrize("status,code", [(401, "MODEL_UNAVAILABLE"), (403, "MODEL_UNAVAILABLE"),
                                          (429, "MODEL_UNAVAILABLE"), (503, "MODEL_UNAVAILABLE"),
                                          (307, "MODEL_UNAVAILABLE")])
def test_http_failures_are_not_retried_or_exposed(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=TEST_KEY, headers={"Location": "https://example.com"})
    model = client(handler)
    try:
        with pytest.raises(ModelError) as caught:
            model.complete([])
        assert caught.value.code == code
        assert TEST_KEY not in str(caught.value)
        assert len(calls) == 1
    finally:
        model.close()


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "MODEL_TIMEOUT"),
                                             (httpx.ConnectError, "MODEL_UNAVAILABLE")])
def test_transport_errors_are_classified_without_retry(exception, code):
    calls = []
    def handler(request):
        calls.append(request)
        raise exception(TEST_KEY, request=request)
    model = client(handler)
    try:
        with pytest.raises(ModelError) as caught:
            model.complete([])
        assert caught.value.code == code
        assert TEST_KEY not in str(caught.value)
        assert len(calls) == 1
    finally:
        model.close()


@pytest.mark.parametrize("body", [
    {}, {"model": "x", "choices": []},
    response(reason="length"), response({"role": "user", "content": "wrong role"}),
    response({"role": "assistant", "content": " "}),
    response({"role": "assistant", "content": None}, reason="tool_calls"),
])
def test_bad_completions_never_pass(body):
    model = client(lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(ModelError, match=".") as caught:
            model.complete([])
        assert caught.value.code == "INVALID_MODEL_OUTPUT"
    finally:
        model.close()


def fixture_handler(*, fault=None):
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(200, json=response())
        if len(calls) == 2:
            data = {"department": "技术部", "period": "2026-Q2", "output_format": "markdown"}
            if fault == "bad_structure":
                data["invented_field"] = True
            return httpx.Response(200, json=response({"role": "assistant", "content": json.dumps(data)}))
        if len(calls) == 3:
            call = {"id": "fixture-call", "type": "function", "function": {
                "name": "run_shell" if fault == "unknown_tool" else "sum_integers",
                "arguments": json.dumps({"values": [3, "5"] if fault == "bad_arguments" else [3, 5]}),
            }}
            if fault == "no_tool":
                return httpx.Response(200, json=response())
            return httpx.Response(200, json=response({"role": "assistant", "content": None,
                "reasoning_content": "private-reasoning-fixture", "tool_calls": [call]}, reason="tool_calls"))
        assert len(calls) == 4
        assert payload["messages"][-2]["reasoning_content"] == "private-reasoning-fixture"
        assert payload["messages"][-1]["tool_call_id"] == "fixture-call"
        result = json.loads(payload["messages"][-1]["content"])
        if fault == "wrong_token":
            result["verification_token"] = "guessed-before-tool-return"
        return httpx.Response(200, json=response({"role": "assistant", "content": json.dumps(result)}))
    return handler, calls


def test_full_mock_roundtrip_saves_evidence_without_reasoning_or_secret(tmp_path):
    handler, calls = fixture_handler()
    code, path = run_verification(settings(), tmp_path, mode="mock", transport=httpx.MockTransport(handler))
    summary = json.loads((path / "summary.json").read_text())
    assert code == 0 and summary["status"] == "succeeded"
    assert summary["mode"] == "mock" and summary["llm_calls"] == 4
    assert set(summary["checks"].values()) == {"passed"}
    assert summary["usage_per_completed_call"] == [None] * 4
    assert summary["cost"] is None
    assert len(calls) == 4
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert len({e["run_id"] for e in events}) == 1
    for file in path.iterdir():
        assert TEST_KEY not in file.read_text()
        assert "private-reasoning-fixture" not in file.read_text()


@pytest.mark.parametrize("fault,expected_calls,step,code", [
    ("bad_structure", 2, "structured", "INVALID_MODEL_OUTPUT"),
    ("no_tool", 3, "tool_roundtrip", "INVALID_MODEL_OUTPUT"),
    ("unknown_tool", 3, "tool_roundtrip", "TOOL_DENIED"),
    ("bad_arguments", 3, "tool_roundtrip", "INVALID_MODEL_OUTPUT"),
    ("wrong_token", 4, "tool_roundtrip", "INVALID_MODEL_OUTPUT"),
])
def test_failed_probe_stops_and_records_reason(tmp_path, fault, expected_calls, step, code):
    handler, calls = fixture_handler(fault=fault)
    result, path = run_verification(settings(), tmp_path, mode="mock", transport=httpx.MockTransport(handler))
    summary = json.loads((path / "summary.json").read_text())
    assert result == 1 and summary["status"] == "failed"
    assert summary["error"]["step"] == step and summary["error"]["code"] == code
    assert len(calls) == expected_calls == summary["llm_calls"]
    if step == "structured":
        assert summary["checks"]["tool_roundtrip"] == "not_run"


def test_injected_transport_cannot_claim_live(tmp_path):
    with pytest.raises(ValueError, match="mock"):
        run_verification(settings(), tmp_path, transport=httpx.MockTransport(lambda r: None))


def test_failed_request_persists_error_and_redacts_echo(tmp_path):
    def handler(request):
        return httpx.Response(401, text=TEST_KEY)
    result, path = run_verification(settings(), tmp_path, mode="mock", transport=httpx.MockTransport(handler))
    summary = json.loads((path / "summary.json").read_text())
    assert result == 1 and summary["llm_calls"] == 1
    assert summary["error"]["code"] == "MODEL_UNAVAILABLE"
    assert '"event_type": "llm_failed"' in (path / "events.jsonl").read_text()
    for file in path.iterdir():
        assert TEST_KEY not in file.read_text()


def test_unexpected_secret_in_model_output_is_redacted(tmp_path):
    def handler(request):
        return httpx.Response(200, json=response({"role": "assistant", "content": TEST_KEY}))
    result, path = run_verification(settings(), tmp_path, mode="mock", transport=httpx.MockTransport(handler))
    assert result == 1
    assert "[REDACTED]" in (path / "events.jsonl").read_text()
    for file in path.iterdir():
        assert TEST_KEY not in file.read_text()
