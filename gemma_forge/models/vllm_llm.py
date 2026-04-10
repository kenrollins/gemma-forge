"""VllmLlm — ADK BaseLlm adapter for local vLLM OpenAI-compatible endpoints.

This adapter lets ADK agents talk to our vLLM instances without
LiteLLM. It converts between ADK's Gemini-format Content/Part types
and the OpenAI Chat Completions API that vLLM serves.

Usage in an ADK Agent:
    from gemma_forge.models.vllm_llm import VllmLlm

    llm = VllmLlm(
        model="gemma-4-31B-IT-NVFP4",
        base_url="http://localhost:8050/v1",
        served_model_name="/weights/Gemma-4-31B-IT-NVFP4",
    )
    agent = Agent(name="architect", model=llm, ...)

The adapter handles:
  - Content → OpenAI messages conversion (user/model → user/assistant)
  - Tool schema → OpenAI function definitions
  - Tool calls in responses → FunctionCall Parts
  - Tool results → function role messages
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any


@contextlib.contextmanager
def _nullcontext():
    """Null context manager for when OTel isn't available."""
    yield None

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class VllmLlm(BaseLlm):
    """ADK LLM adapter for a vLLM OpenAI-compatible endpoint."""

    base_url: str = "http://localhost:8050/v1"
    served_model_name: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048

    def _get_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(base_url=self.base_url, api_key="not-needed")

    # -- Conversion helpers ---------------------------------------------------

    @staticmethod
    def _contents_to_messages(
        contents: list[types.Content],
        system_instruction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convert ADK Content list to OpenAI messages."""
        messages: list[dict[str, Any]] = []

        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        for content in contents:
            role = "assistant" if content.role == "model" else (content.role or "user")

            if not content.parts:
                continue

            for part in content.parts:
                # Text content
                if part.text is not None:
                    messages.append({"role": role, "content": part.text})

                # Tool call from the model
                elif part.function_call is not None:
                    fc = part.function_call
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": fc.id or f"call_{fc.name}",
                            "type": "function",
                            "function": {
                                "name": fc.name,
                                "arguments": json.dumps(fc.args or {}),
                            },
                        }],
                    })

                # Tool response
                elif part.function_response is not None:
                    fr = part.function_response
                    messages.append({
                        "role": "tool",
                        "tool_call_id": fr.id or f"call_{fr.name}",
                        "content": json.dumps(fr.response) if isinstance(fr.response, dict) else str(fr.response),
                    })

        return messages

    @staticmethod
    def _tools_to_functions(
        tools_dict: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        """Convert ADK tool schemas to OpenAI function definitions.

        Extracts parameter schemas from the tool's underlying function
        signature and type annotations, since ADK's FunctionTool API
        varies across versions.
        """
        if not tools_dict:
            return None

        import inspect

        TYPE_MAP = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        functions = []
        for name, tool in tools_dict.items():
            try:
                # Get the description from the tool
                desc = getattr(tool, "description", "") or ""

                # Get the underlying function for signature inspection
                func = getattr(tool, "func", None)
                if func is None:
                    func = getattr(tool, "_func", None)

                properties = {}
                required = []

                if func:
                    sig = inspect.signature(func)
                    # Parse docstring for param descriptions
                    doc = inspect.getdoc(func) or ""
                    param_docs = {}
                    for line in doc.split("\n"):
                        line = line.strip()
                        if line.startswith(":param ") or line.startswith("Args:"):
                            continue
                        # Match "param_name: description" or "param_name (type): description"
                        if ":" in line and not line.startswith("Returns"):
                            parts = line.split(":", 1)
                            pname = parts[0].strip().lstrip("-").strip()
                            pdesc = parts[1].strip() if len(parts) > 1 else ""
                            if pname and pdesc:
                                param_docs[pname] = pdesc

                    for pname, param in sig.parameters.items():
                        if pname in ("self", "cls", "return"):
                            continue
                        ptype = TYPE_MAP.get(param.annotation, "string")
                        properties[pname] = {
                            "type": ptype,
                            "description": param_docs.get(pname, ""),
                        }
                        if param.default is inspect.Parameter.empty:
                            required.append(pname)

                schema = {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }

                functions.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": schema,
                    },
                })
            except Exception as e:
                logger.warning("Failed to convert tool %s: %s", name, e)

        return functions if functions else None

    @staticmethod
    def _response_to_content(
        choice: Any,
    ) -> types.Content:
        """Convert an OpenAI chat completion choice to ADK Content."""
        msg = choice.message
        parts: list[types.Part] = []

        # Text content
        if msg.content:
            parts.append(types.Part(text=msg.content))

        # Tool calls
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": tc.function.arguments}

                parts.append(types.Part(
                    function_call=types.FunctionCall(
                        id=tc.id,
                        name=tc.function.name,
                        args=args,
                    )
                ))

        return types.Content(role="model", parts=parts)

    # -- BaseLlm interface ----------------------------------------------------

    @classmethod
    def supported_models(cls) -> list[str]:
        # We don't register in the global registry — we instantiate directly.
        return []

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Send a request to the vLLM endpoint and yield the response."""
        # Extract system instruction from config
        system_instruction = None
        if llm_request.config and llm_request.config.system_instruction:
            si = llm_request.config.system_instruction
            if isinstance(si, str):
                system_instruction = si
            elif hasattr(si, 'parts') and si.parts:
                system_instruction = " ".join(
                    p.text for p in si.parts if p.text
                )

        messages = self._contents_to_messages(
            llm_request.contents, system_instruction
        )
        tools = self._tools_to_functions(llm_request.tools_dict)

        # Build the request
        model_name = self.served_model_name or self.model
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if llm_request.config:
            if llm_request.config.temperature is not None:
                kwargs["temperature"] = llm_request.config.temperature
            if llm_request.config.max_output_tokens is not None:
                kwargs["max_tokens"] = llm_request.config.max_output_tokens

        if tools:
            kwargs["tools"] = tools

        client = self._get_client()

        # OTel tracing — emit a span for each LLM call with GenAI conventions
        try:
            from gemma_forge.observability.otel import get_tracer, record_token_usage
            tracer = get_tracer("gemma_forge.models")
        except Exception:
            tracer = None

        span_ctx = (
            tracer.start_as_current_span(
                f"llm.{self.model}",
                attributes={
                    "gen_ai.system": "vllm",
                    "gen_ai.request.model": model_name,
                    "gen_ai.request.max_tokens": kwargs.get("max_tokens", 0),
                    "gen_ai.request.temperature": kwargs.get("temperature", 0),
                },
            )
            if tracer
            else _nullcontext()
        )

        with span_ctx as span:
            try:
                response = await client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                content = self._response_to_content(choice)

                prompt_tokens = response.usage.prompt_tokens if response.usage else 0
                completion_tokens = response.usage.completion_tokens if response.usage else 0

                if span:
                    record_token_usage(span, prompt_tokens, completion_tokens)
                    span.set_attribute("gen_ai.response.finish_reasons", [choice.finish_reason or ""])
                    if choice.message.tool_calls:
                        span.set_attribute("gen_ai.response.tool_calls", len(choice.message.tool_calls))

                yield LlmResponse(
                    content=content,
                    turn_complete=choice.finish_reason in ("stop", "length"),
                    custom_metadata={
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                        },
                        "finish_reason": choice.finish_reason,
                    },
                )
            except Exception as e:
                logger.error("vLLM request failed: %s", e)
                if span:
                    span.set_attribute("error", True)
                    span.set_attribute("error.message", str(e))
                yield LlmResponse(
                    error_code="VLLM_ERROR",
                    error_message=str(e),
                )
