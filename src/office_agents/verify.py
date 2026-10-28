"""M1 protocol probe. Run with --live only after configuring a local API key."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from office_agents.config import Settings, load_settings
from office_agents.models.chat import ChatModel, ModelError


class TaskProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    department: Literal["技术部"]
    period: Literal["2026-Q2"]
    output_format: Literal["markdown"]


class SumArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: list[int] = Field(min_length=2, max_length=2)


class ToolAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    total: Literal[8]
    verification_token: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def environment() -> dict:
    packages = {}
    for name in ("sa-office-agents", "httpx", "pydantic", "python-dotenv", "langgraph", "streamlit", "pytest"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "not installed"
    git = {}
    for label, args in (("commit", ["rev-parse", "HEAD"]), ("status", ["status", "--porcelain"])):
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
        git[label] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    # Tie pre-commit validation to the actual public source, even in a dirty tree.
    sources = {}
    candidates = [Path("pyproject.toml"), Path("requirements.txt"), Path("requirements-dev.txt")]
    candidates += sorted(Path("src/office_agents").rglob("*.py"))
    for path in candidates:
        if path.is_file():
            sources[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "python": platform.python_version(), "os": platform.system(),
        "os_version": platform.mac_ver()[0] or platform.release(), "architecture": platform.machine(),
        "packages": packages, "code": git, "source_sha256": sources,
    }


class ProbeRecords:
    def __init__(self, root: Path, settings: Settings, mode: str):
        self.run_id = "m1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
        self.path = root / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.mode = mode
        self.secret = settings.api_key.get_secret_value()
        self.sequence = 0
        self.calls = 0
        self.usage: list[dict | None] = []

    def scrub(self, value):
        if isinstance(value, str):
            return value.replace(self.secret, "[REDACTED]")
        if isinstance(value, dict):
            return {self.scrub(k): self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        return value

    def write(self, filename: str, value: dict) -> None:
        (self.path / filename).write_text(
            json.dumps(self.scrub(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    def event(self, kind: str, *, payload=None, duration_ms=None, usage=None, error=None):
        self.sequence += 1
        item = {
            "schema_version": "1.0", "event_id": uuid4().hex,
            "sequence": self.sequence, "created_at": utc_now(),
            "run_id": self.run_id, "mode": self.mode, "event_type": kind,
            "agent": None, "revision_round": 0, "attempt": 1,
            "payload_refs": [], "payload": payload, "duration_ms": duration_ms,
            "usage": usage, "error": error,
        }
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.scrub(item), ensure_ascii=False) + "\n")


def run_verification(settings: Settings, root: Path, *, mode="live", transport=None) -> tuple[int, Path]:
    if mode not in {"live", "mock"} or (transport is not None and mode != "mock"):
        raise ValueError("替身传输必须明确使用 mock 模式")
    records = ProbeRecords(root, settings, mode)
    started = utc_now()
    clock_start = time.monotonic()
    records.write("manifest.json", {
        "schema_version": "1.0", "scope": "M1 model protocol probe; no role workflow",
        "run_id": records.run_id, "mode": mode, "started_at": started,
        "config": settings.public_config(), "environment": environment(),
        "max_llm_calls": 4, "max_retries_per_call": 0,
        "reasoning_recorded": False,
    })
    records.event("run_started")
    checks = {name: "not_run" for name in ("text", "structured", "tool_roundtrip")}
    client = ChatModel(settings, records.run_id, transport=transport)

    def request(step: str, messages: list[dict], **options):
        if records.calls >= 4:
            raise ModelError("LIMIT_EXCEEDED", "M1 验证最多调用模型四次")
        records.calls += 1
        # Only our synthetic prompts/tool outputs are persisted, never reasoning.
        public_messages = [{k: v for k, v in item.items() if k != "reasoning_content"} for item in messages]
        records.event("llm_requested", payload={"step": step, "messages": public_messages, "options": options})
        call_start = time.monotonic()
        try:
            response = client.complete(messages, **options)
        except ModelError as exc:
            records.event("llm_failed", payload={"step": step},
                          duration_ms=round((time.monotonic() - call_start) * 1000),
                          error={"code": exc.code, "message": str(exc)})
            raise
        # Preserve only service-provided usage counts; absent values stay null.
        usage = None if response.usage is None else {
            key: response.usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        records.usage.append(usage)
        records.event("llm_completed", duration_ms=round((time.monotonic() - call_start) * 1000),
                      usage=usage, payload={"step": step, "response_model": response.model,
                                            "finish_reason": response.choices[0].finish_reason,
                                            "message": response.message.model_dump(exclude_none=True)})
        return response.message

    current = "text"
    error = None
    try:
        message = request("text", [{"role": "user", "content": "This is a coding harness connectivity test. Reply with exactly M1_TEXT_OK."}])
        if message.tool_calls or (message.content or "").strip() != "M1_TEXT_OK":
            raise ModelError("INVALID_MODEL_OUTPUT", "文本探针未收到约定回复")
        checks[current] = "passed"

        current = "structured"
        schema = json.dumps(TaskProbe.model_json_schema(), ensure_ascii=False)
        message = request("structured", [{"role": "user", "content":
            "Test a Python report parser. Extract 技术部, 2026-Q2, markdown into a JSON object. "
            "Use this JSON schema exactly: " + schema}], response_format={"type": "json_object"})
        if message.tool_calls:
            raise ModelError("INVALID_MODEL_OUTPUT", "结构探针意外返回工具调用")
        TaskProbe.model_validate_json(message.content or "")
        checks[current] = "passed"

        current = "tool_roundtrip"
        messages = [{"role": "user", "content":
            "Test the sum_integers Python function using values [3, 5]. Call it exactly once. "
            "After receiving its result, return a JSON object with total and verification_token copied from the tool result."}]
        tools = [{"type": "function", "function": {
            "name": "sum_integers", "description": "Add the two integers in a synthetic unit-test fixture.",
            "parameters": SumArguments.model_json_schema(),
        }}]
        message = request("tool_request", messages, tools=tools, tool_choice="auto")
        if len(message.tool_calls) != 1:
            raise ModelError("INVALID_MODEL_OUTPUT", "模型必须自主请求一次工具调用")
        call = message.tool_calls[0]
        records.event("tool_requested", payload=call.model_dump())
        if call.function.name != "sum_integers":
            raise ModelError("TOOL_DENIED", "模型请求了白名单以外的工具")
        arguments = SumArguments.model_validate_json(call.function.arguments)
        if arguments.values != [3, 5]:
            raise ModelError("TOOL_DENIED", "工具参数不符合测试输入 [3, 5]")
        # The nonce is created only after the model's request. Echoing it proves
        # that the next completion consumed a real program-generated tool result.
        tool_result = {"total": sum(arguments.values), "verification_token": uuid4().hex}
        records.event("tool_completed", payload={"tool_call_id": call.id, "result": tool_result})
        messages.extend([message.for_followup(), {"role": "tool", "tool_call_id": call.id,
                                                "content": json.dumps(tool_result)}])
        final = request("tool_result", messages, response_format={"type": "json_object"})
        if final.tool_calls:
            raise ModelError("INVALID_MODEL_OUTPUT", "工具结果回传后仍请求额外工具")
        answer = ToolAnswer.model_validate_json(final.content or "")
        if answer.verification_token != tool_result["verification_token"]:
            raise ModelError("INVALID_MODEL_OUTPUT", "模型没有正确读取工具返回的随机核验标记")
        checks[current] = "passed"
    except (ModelError, ValidationError) as exc:
        checks[current] = "failed"
        error = {"step": current, "code": exc.code if isinstance(exc, ModelError) else "INVALID_MODEL_OUTPUT",
                 "message": str(exc) if isinstance(exc, ModelError) else "JSON 内容未通过本地严格结构校验"}
        if current == "tool_roundtrip" and checks[current] == "failed":
            records.event("tool_probe_failed", error=error)
    finally:
        client.close()
    status = "failed" if error else "succeeded"
    records.event("run_failed" if error else "run_completed", error=error)
    records.write("summary.json", {
        "schema_version": "1.0", "run_id": records.run_id, "mode": mode,
        "started_at": started, "finished_at": utc_now(),
        "duration_ms": round((time.monotonic() - clock_start) * 1000),
        "status": status, "checks": checks, "llm_calls": records.calls,
        "usage_per_completed_call": records.usage,
        "cost": None, "cost_note": "未核算", "error": error,
        "limitation": "仅验证模型协议，不代表五角色、业务统计或完整 M1 已完成",
    })
    return (1 if error else 0), records.path


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 环境与模型协议验证")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--environment", action="store_true", help="只打印环境版本，不调用模型")
    action.add_argument("--live", action="store_true", help="真实调用已配置模型，成功时共四次请求")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if args.environment:
        print(json.dumps(environment(), ensure_ascii=False, indent=2))
        return 0
    try:
        settings = load_settings(args.env_file)
    except (ValueError, OSError):
        print("配置失败：检查本机 .env 的 MODEL_API_KEY、接口地址及数值参数；未调用模型。", file=sys.stderr)
        return 2
    code, path = run_verification(settings, Path("evidence/raw"))
    summary = json.loads((path / "summary.json").read_text())
    print(json.dumps({"run_id": summary["run_id"], "mode": "live", "status": summary["status"],
                      "checks": summary["checks"], "llm_calls": summary["llm_calls"],
                      "error": summary["error"], "records": str(path)}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
