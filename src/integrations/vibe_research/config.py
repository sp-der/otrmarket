from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


VIBE_VERSION = "0.1.15"
DEFAULT_POLL_SECONDS = 3.0
DEFAULT_MAX_ITER = 12
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_WORKDIR = Path("/app/data/vibe-research")
DEFAULT_EXECUTABLE = Path("/opt/vibe/bin/vibe-trading")

# A RETRY job is claimable while attempts < max_attempts. Once exhausted it
# becomes a terminal ERROR instead of retrying forever against the provider.
DEFAULT_MAX_ATTEMPTS = 3
# Base delay before a RETRY job is claimed again. Doubles per attempt up to
# MAX_RETRY_BACKOFF_SECONDS so a flaky provider is not hammered.
DEFAULT_RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRY_BACKOFF_SECONDS = 900.0

_PROVIDER_KEYS = {
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "kimi": ("KIMI_API_KEY",),
    "kimi-coding": ("KIMI_CODING_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _number(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class VibeResearchConfig:
    enabled: bool
    provider: str
    model: str
    workdir: Path
    executable: Path
    poll_seconds: float
    max_iter: int
    timeout_seconds: int
    max_attempts: int
    retry_backoff_seconds: float

    @property
    def package_installed(self) -> bool:
        return self.executable.exists()

    @property
    def provider_key_names(self) -> tuple[str, ...]:
        return _PROVIDER_KEYS.get(self.provider.lower(), ())

    @property
    def provider_ready(self) -> bool:
        if not self.provider or not self.model:
            return False
        if self.provider.lower() in {"ollama"}:
            return True
        # OAuth-backed providers such as openai-codex require an interactive
        # login and are deliberately not assumed ready in an unattended Railway
        # daemon. Use an API-key provider for automatic replay research.
        if self.provider.lower() == "openai-codex":
            return False
        keys = self.provider_key_names
        return bool(keys and any(os.getenv(name) for name in keys))

    @property
    def provider_status(self) -> str:
        if not self.provider or not self.model:
            return "NOT_CONFIGURED"
        if self.provider.lower() == "openai-codex":
            return "INTERACTIVE_AUTH_REQUIRED"
        if not self.provider_ready:
            return "KEY_NOT_CONFIGURED"
        return "READY"


def load_vibe_research_config() -> VibeResearchConfig:
    explicit = os.getenv("OTR_VIBE_RESEARCH")
    enabled = _truthy(explicit) if explicit is not None else bool(
        os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")
    )
    provider = str(os.getenv("OTR_VIBE_PROVIDER") or os.getenv("LANGCHAIN_PROVIDER") or "").strip()
    model = str(os.getenv("OTR_VIBE_MODEL") or os.getenv("LANGCHAIN_MODEL_NAME") or "").strip()
    workdir = Path(os.getenv("OTR_VIBE_WORKDIR", str(DEFAULT_WORKDIR))).expanduser()
    executable = Path(os.getenv("OTR_VIBE_EXECUTABLE", str(DEFAULT_EXECUTABLE))).expanduser()
    max_iter = max(1, int(_number("OTR_VIBE_MAX_ITER", DEFAULT_MAX_ITER, 1)))
    timeout_seconds = max(30, int(_number("OTR_VIBE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 30)))
    max_attempts = _int("OTR_VIBE_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, 1)
    retry_backoff_seconds = _number(
        "OTR_VIBE_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS, 1.0
    )
    return VibeResearchConfig(
        enabled=enabled,
        provider=provider,
        model=model,
        workdir=workdir,
        executable=executable,
        poll_seconds=_number("OTR_VIBE_POLL_SECONDS", DEFAULT_POLL_SECONDS, 0.5),
        max_iter=max_iter,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def safe_vibe_environment(config: VibeResearchConfig) -> dict[str, str]:
    """Build a narrow env allowlist so OTR secrets never enter Vibe's process."""
    env = {
        "PATH": f"{config.executable.parent}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(config.workdir / "home"),
        "LANG": os.getenv("LANG", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "LANGCHAIN_PROVIDER": config.provider,
        "LANGCHAIN_MODEL_NAME": config.model,
        "VIBE_TRADING_ENABLE_SHELL_TOOLS": "0",
    }
    for name in config.provider_key_names:
        value = os.getenv(name)
        if value:
            env[name] = value
    # Vibe-Trading 0.1.15 supports OpenAI's Responses API and reasoning controls.
    # Forward only these explicit, non-secret knobs so GPT-5.6 function tools do
    # not fall back to an incompatible Chat Completions + reasoning combination.
    for name in (
        "LANGCHAIN_USE_RESPONSES_API",
        "LANGCHAIN_REASONING_EFFORT",
    ):
        value = os.getenv(name)
        if value:
            env[name] = value
    # Provider-specific endpoint overrides are safe to pass, but OTR bridge,
    # dashboard and execution secrets are intentionally absent from this list.
    for name in (
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "OPENROUTER_BASE_URL",
        "OLLAMA_BASE_URL",
    ):
        value = os.getenv(name)
        if value:
            env[name] = value
    return env
