"""Job tool surface and providers: the hub__* tool schemas, deterministic arguments for specialist calls, and the model providers each agent role uses."""
from __future__ import annotations

from typing import Any
from collections.abc import Callable


from ..agents.config import AGENT_ROLES, AgentConfig
from ..agents.providers import (
    PACKAGE_MISSING_MESSAGE, AnthropicProvider, GeminiProvider, ModelProvider, OpenAIProvider,
    provider_package_installed,
)
from ..settings import JobSettings


ENV_SCHEMA = {"type": "string", "enum": ["dev", "stg", "prod"]}
JOB_ID_SCHEMA = {"job_id": {"type": "string"}}
JOB_TOOLS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...], bool], ...] = (
    ("hub__list_projects", "List registered project aliases and their allowed environments without local roots.",
     {}, (), True),
    ("hub__submit_issue", "Start an asynchronous issue investigation using configured logs, source, Jira, and PR "
     "agents. At most {max_queued} jobs can wait in the queue.",
     {"project": {"type": "string"}, "question": {"type": "string"}, "env": ENV_SCHEMA,
      "issue_key": {"type": "string"}, "rcid": {"type": "string"},
      "since": {"type": "string", "format": "date-time"},
      "until": {"type": "string", "format": "date-time"}},
     ("project", "question"), False),
    ("hub__get_job", "Get investigation state, summary, evidence references, and metadata timeline.",
     JOB_ID_SCHEMA, ("job_id",), True),
    ("hub__get_job_detail", "Read one bounded page of a stored, encrypted tool result by job and source ID.",
     {**JOB_ID_SCHEMA, "source_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
      "limit": {"type": "integer", "minimum": 1, "maximum": 10000}},
     ("job_id", "source_id"), True),
    ("hub__provide_job_input", "Supply a validated environment to an investigation waiting for input.",
     {**JOB_ID_SCHEMA, "env": ENV_SCHEMA}, ("job_id", "env"), False),
    ("hub__approve_job", "Approve the exact pending write in a job using its one-time code.",
     {**JOB_ID_SCHEMA, "code": {"type": "string"}}, ("job_id", "code"), False),
    ("hub__cancel_job", "Cancel a queued, waiting, or running investigation.",
     JOB_ID_SCHEMA, ("job_id",), False),
)


def build_deterministic_args(
    role: str, props: dict[str, Any], project: Any, request: dict[str, Any],
) -> dict[str, Any] | None:
    """The arguments a specialist's tool gets without a model: None when the logs role has no target for the env."""
    if role == "logs":
        return _logs_args(props, project, request)
    if role == "sql":
        return _sql_args(props, request)
    return {}


def _logs_args(props: dict[str, Any], project: Any, request: dict[str, Any]) -> dict[str, Any] | None:
    env = request.get("env", "")
    target = project.logs.get(env) if project and project.logs else None
    if target is None:
        return None
    args: dict[str, Any] = {}
    if "organization" in props:
        args["organization"] = target.organization
    if "stream" in props:
        args["stream"] = target.stream
    if "env" in props:
        args["env"] = env
    if "start_time" in props and request.get("since"):
        args["start_time"] = request["since"]
    if "end_time" in props and request.get("until"):
        args["end_time"] = request["until"]
    if "query" in props:
        args["query"] = request.get("question", "")
    return args


def _sql_args(props: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if "query" in props:
        args["query"] = request.get("question", "")
    if "env" in props:
        args["env"] = request.get("env", "")
    return args

def object_schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    schema["additionalProperties"] = False
    return schema

def limit_text(seconds: float) -> str:
    """`15-minute` for whole minutes, else `90-second`: the job window as the job's notes name it."""
    return f"{seconds / 60:g}-minute" if seconds >= 60 and seconds % 60 == 0 else f"{seconds:g}-second"

def create_provider(
    config: AgentConfig, *, prompt_cache: bool = JobSettings().prompt_cache,
    timeout_seconds: float = JobSettings().provider_timeout_seconds,
) -> ModelProvider:
    if not config.configured or not config.provider or not config.model or not config.api_key:
        raise ValueError("Agent is not configured")
    provider_types = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }
    provider_type = provider_types.get(config.provider)
    if provider_type is None:
        raise ValueError("Unsupported provider")
    if not provider_package_installed(config.provider):
        raise ValueError(PACKAGE_MISSING_MESSAGE)
    if provider_type is AnthropicProvider:
        return AnthropicProvider(
            config.model, config.api_key, prompt_cache=prompt_cache, timeout_seconds=timeout_seconds,
        )
    return provider_type(config.model, config.api_key, timeout_seconds=timeout_seconds)

def create_providers(
    agents: dict[str, AgentConfig], provider_factory: Callable[[AgentConfig], ModelProvider],
) -> dict[str, ModelProvider]:
    providers: dict[str, ModelProvider] = {}
    for role in AGENT_ROLES:
        config = agents.get(role)
        if config and config.configured:
            try:
                providers[role] = provider_factory(config)
            except Exception:
                # A broken optional role must not prevent direct tools or other roles from starting.
                continue
    return providers
