"""Small Chat Completions adapter; no implicit retries or mock fallback."""

from typing import Any
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from office_agents.config import Settings


class ModelError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(min_length=1)
    type: str
    function: FunctionCall


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Some reasoning models require this field when echoing a tool request.
    # It is forwarded in memory only, never written into experiment records.
    reasoning_content: str | None = Field(default=None, repr=False, exclude=True)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def absent_tools(cls, value):
        # Compatible providers may explicitly return null for this optional field.
        return [] if value is None else value

    def for_followup(self) -> dict:
        result = self.model_dump(exclude_none=True)
        if not self.tool_calls:
            result.pop("tool_calls", None)
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        return result


class Choice(BaseModel):
    message: Message
    finish_reason: str


class Completion(BaseModel):
    model: str = Field(min_length=1)
    choices: list[Choice] = Field(min_length=1, max_length=1)
    usage: dict[str, Any] | None = None

    @property
    def message(self) -> Message:
        return self.choices[0].message


class ChatModel:
    def __init__(self, settings: Settings, session_id: str, *, transport=None):
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.base_url + "/",
            headers={
                "Authorization": "Bearer " + settings.api_key.get_secret_value(),
                "User-Agent": "SA-Project-M1/0.1",
                "x-opencode-session": session_id,
            },
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def complete(self, messages: list[dict], **options) -> Completion:
        payload = {
            "model": self.settings.model, "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_output_tokens,
            **options,
        }
        try:
            response = self.client.post("chat/completions", json=payload)
        except httpx.TimeoutException:
            raise ModelError("MODEL_TIMEOUT", "模型请求超时；未自动重试") from None
        except httpx.HTTPError:
            raise ModelError("MODEL_UNAVAILABLE", "模型连接失败；检查本机网络和代理") from None
        if response.status_code >= 300:
            # Do not print the response body: it can echo credentials or request data.
            raise ModelError("MODEL_UNAVAILABLE", f"模型服务返回 HTTP {response.status_code}；未自动重试")
        try:
            result = Completion.model_validate(response.json())
        except (ValueError, ValidationError):
            raise ModelError("INVALID_MODEL_OUTPUT", "服务未返回合法的 Chat Completions 结构") from None
        choice = result.choices[0]
        if choice.message.role != "assistant" or choice.finish_reason not in {"stop", "tool_calls"}:
            raise ModelError("INVALID_MODEL_OUTPUT", "角色异常或输出被截断/阻止")
        if bool(choice.message.tool_calls) != (choice.finish_reason == "tool_calls"):
            raise ModelError("INVALID_MODEL_OUTPUT", "工具请求与结束原因不一致")
        if not choice.message.tool_calls and not (choice.message.content or "").strip():
            raise ModelError("INVALID_MODEL_OUTPUT", "模型未返回正文")
        if any(call.type != "function" for call in choice.message.tool_calls):
            raise ModelError("INVALID_MODEL_OUTPUT", "模型返回了不支持的工具类型")
        return result
