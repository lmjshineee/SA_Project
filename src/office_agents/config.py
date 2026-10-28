"""Load local model configuration without putting secrets in public records."""

from pathlib import Path
from urllib.parse import urlsplit
import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    provider: str = "opencode-go"
    model: str = "deepseek-v4.1-flash"
    base_url: str = "https://opencode.ai/inference/go/openai/v1"
    api_key: SecretStr = Field(repr=False)
    temperature: float = Field(default=0, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=16384)
    request_timeout_seconds: float = Field(default=60, gt=0, le=120)

    @field_validator("provider", "model")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("配置不能为空")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("请在本机 .env 中填写 MODEL_API_KEY")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        # Accessing port validates its syntax and range before HTTP client setup.
        parsed.port
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("接口地址必须是无凭据、查询参数和片段的 HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程模型接口必须使用 HTTPS")
        return value.rstrip("/")

    def public_config(self) -> dict:
        return self.model_dump(exclude={"api_key"})


def load_settings(env_file: Path = Path(".env")) -> Settings:
    # The explicit path avoids loading a parent directory's .env by accident.
    load_dotenv(env_file, override=False)
    names = {
        "provider": "MODEL_PROVIDER", "model": "MODEL_NAME",
        "base_url": "MODEL_BASE_URL", "api_key": "MODEL_API_KEY",
        "temperature": "MODEL_TEMPERATURE",
        "max_output_tokens": "MODEL_MAX_OUTPUT_TOKENS",
        "request_timeout_seconds": "MODEL_TIMEOUT_SECONDS",
    }
    return Settings(**{field: os.environ[key] for field, key in names.items() if key in os.environ})
