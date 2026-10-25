"""
AI tiers for CAD Studio.

The AI never draws anything. It reads the chat and writes the part spec (conversation.py),
and the CadQuery templates build the solid. So a stronger model only plans better, and
swapping one in cannot change what the CAD engine does.

  Engine    No AI                          (built-in rules, straight to the CAD engine)
  Basic     DeepSeek                       (the original planner)
  Plus      GPT or Qwen                    (the person picks one)
  Advanced  Claude

Each tier has a daily allowance of AI messages and a Generate price in credits (see
config.py). A model only shows up as available when its key and model name are set, so a
tier can be switched on later by adding environment variables, with no code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

TIERS = ("engine", "basic", "plus", "advanced")
TIER_LABELS = {"engine": "Engine", "basic": "Basic", "plus": "Plus", "advanced": "Advanced"}
DEFAULT_MODEL_ID = "basic:deepseek"

LLM = Callable[[list[dict]], str]


@dataclass(frozen=True)
class ModelSpec:
    id: str          # what the front end sends, for example "plus:gpt"
    tier: str        # basic | plus | advanced
    provider: str    # rules | deepseek | openai | qwen | anthropic
    label: str       # what the person sees, for example "GPT"
    available: bool  # key and model name are configured


def tier_label(tier: str) -> str:
    return TIER_LABELS.get(tier, "Basic")


def messages_per_day(settings: Any, tier: str) -> int | None:
    """Daily AI messages for a tier, or None for no limit. Engine never calls an AI, so it has none."""
    if tier == "engine":
        return None
    return int(getattr(settings, f"AI_MESSAGES_PER_DAY_{(tier if tier in TIERS else 'basic').upper()}"))


def pricing_tier(spec: ModelSpec, source: str) -> str:
    """The tier a version is priced at. It follows what actually planned the part: if the AI was
    picked but failed and the built-in rules took over, the person pays the Engine price."""
    return spec.tier if source == "llm" else "engine"


def generate_credits(settings: Any, tier: str | None) -> int:
    """What pressing Generate costs for a version planned by this tier. Unknown or missing
    tiers (older jobs) are priced as Basic."""
    t = tier if tier in TIERS else "basic"
    return int(getattr(settings, f"GENERATE_CREDITS_{t.upper()}"))


def catalogue(settings: Any) -> list[ModelSpec]:
    return [
        ModelSpec("engine:rules", "engine", "rules", "No AI (engine only)", True),
        ModelSpec("basic:deepseek", "basic", "deepseek", "DeepSeek", True),
        ModelSpec("plus:gpt", "plus", "openai", "GPT",
                  bool(settings.OPENAI_API_KEY and settings.OPENAI_MODEL)),
        ModelSpec("plus:qwen", "plus", "qwen", "Qwen",
                  bool(settings.QWEN_API_KEY and settings.QWEN_MODEL)),
        ModelSpec("advanced:claude", "advanced", "anthropic", "Claude",
                  bool(settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_MODEL)),
    ]


def find(settings: Any, model_id: str | None) -> ModelSpec | None:
    wanted = model_id or DEFAULT_MODEL_ID
    return next((m for m in catalogue(settings) if m.id == wanted), None)


def model_name(spec: ModelSpec, settings: Any) -> str | None:
    """The provider's own model string, kept on the job for provenance."""
    return {
        "rules": None,
        "deepseek": settings.CHAT_PLANNER_MODEL or settings.DEEPSEEK_DEFAULT_MODEL,
        "openai": settings.OPENAI_MODEL,
        "qwen": settings.QWEN_MODEL,
        "anthropic": settings.ANTHROPIC_MODEL,
    }.get(spec.provider)


def make_llm(spec: ModelSpec, settings: Any) -> LLM | None:
    """The callable the planner uses, or None for Basic with no DeepSeek key (the planner then
    uses its free built-in rules, as it always did)."""
    timeout = float(settings.AI_TIMEOUT_SECONDS)
    if spec.provider == "rules":
        return None
    if spec.provider == "deepseek":
        if not settings.DEEPSEEK_API_KEY:
            return None
        return _openai_compatible(
            settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_BASE_URL,
            settings.CHAT_PLANNER_MODEL or settings.DEEPSEEK_DEFAULT_MODEL,
            float(settings.DEEPSEEK_TIMEOUT_SECONDS), temperature=0.1,
        )
    if spec.provider == "openai":
        # No temperature: some GPT models only accept their default.
        return _openai_compatible(settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL,
                                  settings.OPENAI_MODEL, timeout, temperature=None)
    if spec.provider == "qwen":
        return _openai_compatible(settings.QWEN_API_KEY, settings.QWEN_BASE_URL,
                                  settings.QWEN_MODEL, timeout, temperature=0.1)
    if spec.provider == "anthropic":
        return _anthropic(settings.ANTHROPIC_API_KEY, settings.ANTHROPIC_MODEL, timeout)
    raise ValueError(f"unknown provider {spec.provider!r}")


def _openai_compatible(api_key: str, base_url: str, model: str, timeout: float, *, temperature: float | None) -> LLM:
    """DeepSeek, GPT and Qwen all speak the OpenAI chat API, so one client covers them."""
    def call(messages: list[dict]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        kwargs: dict[str, Any] = {"model": model, "messages": messages,
                                  "response_format": {"type": "json_object"}}
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    return call


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """Claude takes the system prompt separately and wants user and assistant turns that
    alternate, starting with the user."""
    system = "\n\n".join(str(m["content"]) for m in messages if m.get("role") == "system")
    turns: list[dict] = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        if turns and turns[-1]["role"] == m["role"]:
            turns[-1]["content"] += "\n\n" + str(m["content"])
        else:
            turns.append({"role": m["role"], "content": str(m["content"])})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": "(conversation start)"})
    return system, turns


def _anthropic(api_key: str, model: str, timeout: float) -> LLM:
    def call(messages: list[dict]) -> str:
        import httpx

        system, turns = to_anthropic(messages)
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 1200, "system": system, "messages": turns},
            timeout=timeout,
        )
        resp.raise_for_status()
        return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
    return call
