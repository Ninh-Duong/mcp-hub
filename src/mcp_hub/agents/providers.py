from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from typing import Any, Protocol


# One request is retried at most this many times by the SDK, each attempt bounded by the provider timeout.
MAX_RETRIES = 2
DEFAULT_TIMEOUT_SECONDS = 60.0


# The SDK package each provider imports lazily on its first request (optional investigation dependencies).
PROVIDER_PACKAGES = {"openai": "openai", "anthropic": "anthropic", "gemini": "google.genai"}
PACKAGE_MISSING_MESSAGE = "provider package not installed"


def provider_package_installed(provider: str) -> bool:
    """Whether the SDK package for `provider` can be imported; unknown providers have none and report False."""
    package = PROVIDER_PACKAGES.get(provider)
    if package is None:
        return False
    try:
        return importlib.util.find_spec(package) is not None
    except ImportError:  # a missing parent package such as `google`
        return False


class ProviderError(RuntimeError):
    """Safe provider failure. The underlying SDK message is deliberately omitted."""


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    text: str
    tool_calls: tuple[ModelToolCall, ...] = ()


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any],
        *,
        max_output_tokens: int = 2048,
    ) -> ModelTurn: ...


class _Provider:
    provider_name = "model"

    def __init__(
        self, model: str, api_key: str, *, client: Any = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, max_retries: int = MAX_RETRIES,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r})"

    def _safe_error(self, exc: Exception) -> ProviderError:
        return ProviderError(f"{self.provider_name} request failed ({type(exc).__name__})")


class OpenAIProvider(_Provider):
    provider_name = "OpenAI"

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[Any], *, max_output_tokens: int = 2048,
    ) -> ModelTurn:
        try:
            client = self._client or self._create_client()
            response = await client.responses.create(
                model=self.model,
                input=_openai_messages(messages),
                tools=[_openai_tool(tool) for tool in tools],
                max_output_tokens=max_output_tokens,
                store=False,
            )
            return _parse_openai(response)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from None

    def _create_client(self) -> Any:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout_seconds, max_retries=self.max_retries)
        return self._client


class AnthropicProvider(_Provider):
    provider_name = "Anthropic"

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[Any], *, max_output_tokens: int = 2048,
    ) -> ModelTurn:
        try:
            client = self._client or self._create_client()
            request_messages, system = _anthropic_messages(messages)
            response = await client.messages.create(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=request_messages,
                tools=[_anthropic_tool(tool) for tool in tools],
            )
            return _parse_anthropic(response)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from None

    def _create_client(self) -> Any:
        import anthropic

        self._client = anthropic.AsyncAnthropic(
            api_key=self.api_key, timeout=self.timeout_seconds, max_retries=self.max_retries,
        )
        return self._client


class GeminiProvider(_Provider):
    provider_name = "Gemini"

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[Any], *, max_output_tokens: int = 2048,
    ) -> ModelTurn:
        try:
            client = self._client or self._create_client()
            model_client = client.aio.models if hasattr(client, "aio") else client.models
            contents, system = _gemini_messages(messages)
            declarations = [_gemini_tool(tool) for tool in tools]
            config = {"max_output_tokens": max_output_tokens}
            if system:
                config["system_instruction"] = system
            if declarations:
                config["tools"] = [{"function_declarations": declarations}]
            response = await model_client.generate_content(model=self.model, contents=contents, config=config)
            return _parse_gemini(response)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from None

    def _create_client(self) -> Any:
        from google import genai

        self._client = genai.Client(
            api_key=self.api_key,
            http_options={
                "timeout": int(self.timeout_seconds * 1000),
                # `attempts` counts the first request too.
                "retry_options": {"attempts": self.max_retries + 1},
            },
        )
        return self._client


def _tool_fields(tool: Any) -> tuple[str, str, dict[str, Any]]:
    if isinstance(tool, dict):
        name = tool.get("name")
        description = tool.get("description") or ""
        schema = tool.get("input_schema") or tool.get("inputSchema") or tool.get("parameters") or {"type": "object"}
    else:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None) or ""
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object"}
    if not isinstance(name, str) or not name:
        raise ProviderError("Tool declaration has no valid name")
    if not isinstance(schema, dict):
        raise ProviderError(f"Tool '{name}' has an invalid input schema")
    return name, description, schema


def _openai_tool(tool: Any) -> dict[str, Any]:
    name, description, schema = _tool_fields(tool)
    return {"type": "function", "name": name, "description": description, "parameters": schema, "strict": False}


def _anthropic_tool(tool: Any) -> dict[str, Any]:
    name, description, schema = _tool_fields(tool)
    return {"name": name, "description": description, "input_schema": schema}


def _gemini_tool(tool: Any) -> dict[str, Any]:
    name, description, schema = _tool_fields(tool)
    return {"name": name, "description": description, "parameters": schema}


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            converted.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id", ""),
                "output": str(message.get("content", "")),
            })
        elif role == "assistant" and message.get("tool_calls"):
            for call in message["tool_calls"]:
                converted.append({
                    "type": "function_call", "call_id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                })
            if message.get("content"):
                converted.append({"role": "assistant", "content": message["content"]})
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "tool":
            converted.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": message.get("tool_call_id", ""),
                "content": str(message.get("content", "")),
            }]})
        elif role == "assistant" and message.get("tool_calls"):
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            for call in message["tool_calls"]:
                blocks.append({
                    "type": "tool_use", "id": call.get("id", ""),
                    "name": call.get("name", ""), "input": call.get("arguments", {}),
                })
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted, system


def _gemini_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "tool":
            name = message.get("name", "")
            try:
                response = json.loads(str(message.get("content", "")))
            except json.JSONDecodeError:
                response = {"result": str(message.get("content", ""))}
            converted.append({"role": "user", "parts": [{"function_response": {"name": name, "response": response}}]})
        elif role == "assistant" and message.get("tool_calls"):
            parts = []
            if message.get("content"):
                parts.append({"text": str(message["content"])})
            for call in message["tool_calls"]:
                parts.append({"function_call": {
                    "id": call.get("id", ""), "name": call.get("name", ""), "args": call.get("arguments", {}),
                }})
            converted.append({"role": "model", "parts": parts})
        else:
            converted.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": str(message.get("content", ""))}]})
    return converted, system


def _parse_openai(response: Any) -> ModelTurn:
    text_parts: list[str] = []
    calls: list[ModelToolCall] = []
    for item in getattr(response, "output", ()) or ():
        if getattr(item, "type", None) == "function_call":
            arguments = getattr(item, "arguments", "{}")
            calls.append(ModelToolCall(
                str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                str(getattr(item, "name", "")), _parse_arguments(arguments),
            ))
        elif getattr(item, "type", None) == "message":
            for content in getattr(item, "content", ()) or ():
                if getattr(content, "type", None) in {"output_text", "text"}:
                    text = getattr(content, "text", "")
                    if text:
                        text_parts.append(str(text))
    if not text_parts and getattr(response, "output_text", None):
        text_parts.append(str(response.output_text))
    return ModelTurn("\n".join(text_parts), tuple(calls))


def _parse_anthropic(response: Any) -> ModelTurn:
    text_parts: list[str] = []
    calls: list[ModelToolCall] = []
    for block in getattr(response, "content", ()) or ():
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            text_parts.append(str(block.text))
        elif getattr(block, "type", None) == "tool_use":
            arguments = getattr(block, "input", {})
            if not isinstance(arguments, dict):
                raise ProviderError("Anthropic returned malformed tool arguments")
            calls.append(ModelToolCall(str(getattr(block, "id", "")), str(getattr(block, "name", "")), arguments))
    return ModelTurn("\n".join(text_parts), tuple(calls))


def _parse_gemini(response: Any) -> ModelTurn:
    text_parts: list[str] = []
    calls: list[ModelToolCall] = []
    candidates = getattr(response, "candidates", ()) or ()
    if candidates:
        parts = getattr(getattr(candidates[0], "content", None), "parts", ()) or ()
        for part in parts:
            if getattr(part, "text", None):
                text_parts.append(str(part.text))
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                arguments = getattr(function_call, "args", {}) or {}
                if not isinstance(arguments, dict):
                    raise ProviderError("Gemini returned malformed tool arguments")
                calls.append(ModelToolCall(
                    str(getattr(function_call, "id", "")),
                    str(getattr(function_call, "name", "")), arguments,
                ))
    if not text_parts and getattr(response, "text", None):
        text_parts.append(str(response.text))
    return ModelTurn("\n".join(text_parts), tuple(calls))


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise ProviderError("Model returned malformed tool arguments") from None
    if not isinstance(parsed, dict):
        raise ProviderError("Model returned malformed tool arguments")
    return parsed
