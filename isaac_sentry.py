"""
Isaac – Full Sentry observability (errors, traces, profiles, logs, metrics, gen_ai)

No-op when SENTRY_DSN is unset. Manual gen_ai spans for the custom aiohttp relay.

Env:
  SENTRY_DSN                      — enable Sentry (required)
  SENTRY_TRACES_SAMPLE_RATE       — default 1.0 (full tracing)
  SENTRY_PROFILES_SAMPLE_RATE     — default same as traces (or 1.0)
  SENTRY_PROFILE_SESSION_SAMPLE_RATE — continuous profiling session (default 1.0)
  SENTRY_ERROR_SAMPLE_RATE        — default 1.0
  SENTRY_ENVIRONMENT              — development / production
  SENTRY_RELEASE                  — default isaac@5.3
  SENTRY_INCLUDE_PROMPTS          — default 1 (PII for AI spans)
  SENTRY_ENABLE_LOGS              — default 1
  SENTRY_ENABLE_METRICS           — default 1
  SENTRY_DEBUG                    — default 0
  SENTRY_ATTACH_STACKTRACE        — default 1

Docs:
  https://docs.sentry.io/platforms/python/
  https://docs.sentry.io/platforms/python/tracing/
  https://docs.sentry.io/platforms/python/profiling/
  https://docs.sentry.io/platforms/python/logs/
  AI: custom-instrumentation/ai-agents-module
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

log = logging.getLogger("Isaac.Sentry")

_initialized = False
_include_prompts = True
_session_conversation_id: Optional[str] = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def is_enabled() -> bool:
    return _initialized


def include_prompts() -> bool:
    return _include_prompts and _initialized


def _build_integrations() -> list[Any]:
    """Enable as many first-party integrations as are installed."""
    integrations: list[Any] = []
    try:
        from sentry_sdk.integrations.logging import LoggingIntegration

        integrations.append(
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
            )
        )
    except Exception:
        pass

    # Stdlib / asyncio / threading — usually always present
    for mod_path, cls_name in (
        ("sentry_sdk.integrations.stdlib", "StdlibIntegration"),
        ("sentry_sdk.integrations.excepthook", "ExcepthookIntegration"),
        ("sentry_sdk.integrations.dedupe", "DedupeIntegration"),
        ("sentry_sdk.integrations.atexit", "AtexitIntegration"),
        ("sentry_sdk.integrations.modules", "ModulesIntegration"),
        ("sentry_sdk.integrations.argv", "ArgvIntegration"),
        ("sentry_sdk.integrations.threading", "ThreadingIntegration"),
        ("sentry_sdk.integrations.asyncio", "AsyncioIntegration"),
    ):
        try:
            import importlib

            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            if cls_name == "ThreadingIntegration":
                try:
                    integrations.append(cls(propagate_scope=True))
                except TypeError:
                    integrations.append(cls())
            else:
                integrations.append(cls())
        except Exception:
            continue

    # Optional HTTP stacks used by Isaac
    for mod_path, cls_name, kwargs in (
        ("sentry_sdk.integrations.aiohttp", "AioHttpIntegration", {}),
        ("sentry_sdk.integrations.httpx", "HttpxIntegration", {}),
        ("sentry_sdk.integrations.flask", "FlaskIntegration", {}),
    ):
        try:
            import importlib

            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            integrations.append(cls(**kwargs) if kwargs else cls())
        except Exception:
            continue

    return integrations


_EXPECTED_SHUTDOWN_TYPES = frozenset({
    "KeyboardInterrupt",
    "SystemExit",
    "CancelledError",  # asyncio cancel during redeploy
    "asyncio.exceptions.CancelledError",
})


def _exception_type_names(event: dict[str, Any], hint: dict[str, Any]) -> list[str]:
    names: list[str] = []
    try:
        exc_info = hint.get("exc_info") if isinstance(hint, dict) else None
        if exc_info and len(exc_info) >= 1 and exc_info[0] is not None:
            t = exc_info[0]
            names.append(getattr(t, "__name__", "") or str(t))
            names.append(f"{getattr(t, '__module__', '')}.{getattr(t, '__name__', '')}")
    except Exception:
        pass
    try:
        for item in (event.get("exception") or {}).get("values") or []:
            if isinstance(item, dict):
                t = str(item.get("type") or "").strip()
                if t:
                    names.append(t)
    except Exception:
        pass
    return [n for n in names if n]


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Drop expected noise (Ollama offline, restarts) — keep real bugs."""
    try:
        # Shutdown / redeploy noise — filter by exception type, not only message text
        for tname in _exception_type_names(event, hint or {}):
            short = tname.rsplit(".", 1)[-1]
            if tname in _EXPECTED_SHUTDOWN_TYPES or short in _EXPECTED_SHUTDOWN_TYPES:
                return None

        msg = ""
        if event.get("logentry") and isinstance(event["logentry"], dict):
            msg = str(event["logentry"].get("message") or "")
        if not msg and event.get("message"):
            msg = str(event.get("message") or "")
        if not msg:
            exc = (event.get("exception") or {}).get("values") or []
            if exc and isinstance(exc[0], dict):
                msg = str(exc[0].get("value") or "")
                if not msg:
                    msg = str(exc[0].get("type") or "")
        # title often equals type name for bare interrupts
        if not msg and event.get("title"):
            msg = str(event.get("title") or "")
        ml = msg.lower()
        # Expected local/free backend absence
        ollama_noise = (
            "ollama nicht erreichbar" in ml
            or ("ollama" in ml and "nicht erreichbar" in ml)
            or ("ollama" in ml and "connection refused" in ml)
            or ("ollama" in ml and "timeout" in ml)
            or ("ollama" in ml and "failed to connect" in ml)
        )
        if ollama_noise:
            free = _env_bool("ISAAC_FREE_CLOUD", False)
            primary = (os.getenv("ACTIVE_PROVIDER") or os.getenv("ISAAC_PRIMARY_PROVIDER") or "").strip().lower()
            if free or primary not in {"", "ollama"}:
                return None
        # Intentional verify smokes
        if "isaac_sentry_verify" in ml or ("isaac sentry" in ml and "smoke" in ml):
            return None
        # KeyboardInterrupt / SystemExit from restarts (message path)
        if "keyboardinterrupt" in ml or "systemexit" in ml:
            return None
        # WS probe noise (health checkers without Upgrade header)
        if "invalidupgrade" in ml or "missing connection header" in ml:
            return None
    except Exception:
        pass
    return event


def init_sentry() -> bool:
    """Initialize Sentry once with tracing, profiling, logs, metrics, AI spans."""
    global _initialized, _include_prompts, _session_conversation_id

    if _initialized:
        return True

    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        log.info("Sentry: SENTRY_DSN not set — observability disabled")
        return False

    try:
        import sentry_sdk
    except ImportError:
        log.warning("Sentry: sentry-sdk not installed (pip install 'sentry-sdk>=2.60.0')")
        return False

    _include_prompts = _env_bool("SENTRY_INCLUDE_PROMPTS", True)

    environment = (
        os.getenv("SENTRY_ENVIRONMENT")
        or os.getenv("ISAAC_ENV")
        or ("production" if _env_bool("ISAAC_FREE_CLOUD", False) else "development")
    ).strip() or "development"

    # Full tracing by default (user-requested); override via env for cost control
    traces = max(0.0, min(1.0, _env_float("SENTRY_TRACES_SAMPLE_RATE", 1.0)))
    profiles = max(
        0.0,
        min(1.0, _env_float("SENTRY_PROFILES_SAMPLE_RATE", traces)),
    )
    profile_session = max(
        0.0,
        min(1.0, _env_float("SENTRY_PROFILE_SESSION_SAMPLE_RATE", 1.0)),
    )
    error_rate = max(0.0, min(1.0, _env_float("SENTRY_ERROR_SAMPLE_RATE", 1.0)))

    init_kwargs: dict[str, Any] = {
        "dsn": dsn,
        "environment": environment,
        "release": os.getenv("SENTRY_RELEASE", "isaac@5.3"),
        # Errors
        "sample_rate": error_rate,
        "attach_stacktrace": _env_bool("SENTRY_ATTACH_STACKTRACE", True),
        "send_default_pii": _include_prompts,
        "max_breadcrumbs": int(_env_float("SENTRY_MAX_BREADCRUMBS", 100)),
        "debug": _env_bool("SENTRY_DEBUG", False),
        "before_send": _before_send,
        # SDK-level ignore for restart/redeploy noise (also covered by before_send)
        "ignore_errors": [
            KeyboardInterrupt,
            SystemExit,
        ],
        # Tracing (Performance)
        "enable_tracing": True,
        "traces_sample_rate": traces,
        "propagate_traces": True,
        # AI agent spans as standalone envelopes
        "stream_gen_ai_spans": True,
        # Integrations
        "integrations": _build_integrations(),
        "default_integrations": True,
    }

    # Profiling (transaction + continuous session when supported)
    init_kwargs["profiles_sample_rate"] = profiles
    init_kwargs["profile_session_sample_rate"] = profile_session
    init_kwargs["profile_lifecycle"] = os.getenv("SENTRY_PROFILE_LIFECYCLE", "trace")

    # Structured logs + metrics (SDK 2.x)
    if _env_bool("SENTRY_ENABLE_LOGS", True):
        init_kwargs["enable_logs"] = True
    if _env_bool("SENTRY_ENABLE_METRICS", True):
        init_kwargs["enable_metrics"] = True

    # Drop unknown options for older SDKs by progressive retry
    optional_keys = [
        "stream_gen_ai_spans",
        "profile_session_sample_rate",
        "profile_lifecycle",
        "profiles_sample_rate",
        "enable_logs",
        "enable_metrics",
        "enable_tracing",
        "propagate_traces",
        "ignore_errors",
    ]
    while True:
        try:
            sentry_sdk.init(**init_kwargs)
            break
        except TypeError as exc:
            msg = str(exc)
            dropped = False
            for key in list(optional_keys):
                if key in msg or key in init_kwargs:
                    if key in init_kwargs:
                        init_kwargs.pop(key, None)
                        optional_keys.remove(key) if key in optional_keys else None
                        dropped = True
                        log.debug("Sentry init: dropped unsupported option %s", key)
                        break
            if not dropped:
                # Drop last optional still present
                if optional_keys and optional_keys[0] in init_kwargs:
                    init_kwargs.pop(optional_keys.pop(0), None)
                    continue
                log.warning("Sentry init failed: %s", exc)
                return False

    _initialized = True
    _session_conversation_id = f"isaac-{uuid.uuid4().hex[:16]}"
    set_conversation_id(_session_conversation_id)

    owner = (os.getenv("ISAAC_OWNER") or "owner").strip() or "owner"
    try:
        sentry_sdk.set_user({"id": owner, "username": owner})
        sentry_sdk.set_tag("service", "isaac-kernel")
        sentry_sdk.set_tag("component", "kernel")
        sentry_sdk.set_context(
            "isaac",
            {
                "version": os.getenv("SENTRY_RELEASE", "isaac@5.3"),
                "free_cloud": _env_bool("ISAAC_FREE_CLOUD", False),
            },
        )
    except Exception:
        pass

    log.info(
        "Sentry full stack active (env=%s traces=%s profiles=%s errors=%s logs=%s)",
        environment,
        traces,
        profiles,
        error_rate,
        init_kwargs.get("enable_logs", False),
    )
    return True


def session_conversation_id() -> Optional[str]:
    return _session_conversation_id


def set_conversation_id(conversation_id: str) -> None:
    """Attach gen_ai.conversation.id for multi-turn Conversations view."""
    if not _initialized or not conversation_id:
        return
    try:
        import sentry_sdk.ai

        sentry_sdk.ai.set_conversation_id(str(conversation_id))
    except Exception:
        try:
            import sentry_sdk

            sentry_sdk.set_tag("conversation_id", str(conversation_id)[:120])
        except Exception:
            pass


@contextmanager
def request_transaction(
    *,
    name: str = "isaac.process",
    op: str = "function",
    user_input: str = "",
) -> Iterator[Any]:
    """Root performance transaction for one kernel turn (enables full traces)."""
    if not _initialized:
        yield _NoopSpan()
        return

    import sentry_sdk

    with sentry_sdk.start_transaction(op=op, name=name) as tx:
        try:
            if user_input:
                tx.set_tag("input_preview", (user_input or "")[:80])
                tx.set_data("isaac.input_chars", len(user_input or ""))
            sentry_sdk.set_tag("transaction_name", name)
        except Exception:
            pass
        yield tx


def capture_exception(exc: BaseException, **scope_kwargs: Any) -> None:
    if not _initialized:
        return
    try:
        import sentry_sdk

        if scope_kwargs:
            with sentry_sdk.push_scope() as scope:
                for k, v in scope_kwargs.items():
                    scope.set_extra(k, v)
                sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def capture_message(message: str, level: str = "info", **tags: Any) -> None:
    if not _initialized:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, str(v)[:200])
            sentry_sdk.capture_message(message, level=level)
    except Exception:
        pass


def add_breadcrumb(message: str, category: str = "isaac", level: str = "info", **data: Any) -> None:
    if not _initialized:
        return
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            message=message[:500],
            category=category,
            level=level,
            data={k: str(v)[:300] for k, v in data.items()},
        )
    except Exception:
        pass


def _messages_json(system: str, user_prompt: str) -> str:
    msgs: list[dict[str, Any]] = []
    if system:
        msgs.append(
            {
                "role": "system",
                "parts": [{"type": "text", "content": system[:8000]}],
            }
        )
    msgs.append(
        {
            "role": "user",
            "parts": [{"type": "text", "content": (user_prompt or "")[:12000]}],
        }
    )
    return json.dumps(msgs, ensure_ascii=False)


def _output_json(text: str) -> str:
    return json.dumps(
        [
            {
                "role": "assistant",
                "parts": [{"type": "text", "content": (text or "")[:12000]}],
            }
        ],
        ensure_ascii=False,
    )


@contextmanager
def gen_ai_chat_span(
    *,
    model: str,
    provider: str,
    system: str = "",
    prompt: str = "",
    agent_name: str = "Isaac",
) -> Iterator[Any]:
    """Manual gen_ai.chat span around a relay LLM call."""
    if not _initialized:
        yield _NoopSpan()
        return

    import sentry_sdk

    model_name = (model or "unknown").strip() or "unknown"
    provider_name = (provider or "unknown").strip().lower() or "unknown"
    name = f"chat {model_name}"

    with sentry_sdk.start_span(op="gen_ai.chat", name=name) as span:
        try:
            span.set_data("gen_ai.operation.name", "chat")
            span.set_data("gen_ai.request.model", model_name)
            span.set_data("gen_ai.response.model", model_name)
            span.set_data("gen_ai.provider.name", provider_name)
            span.set_data("gen_ai.agent.name", agent_name)
            span.set_data("gen_ai.pipeline.name", "isaac-relay")
            if _include_prompts:
                if system:
                    span.set_data("gen_ai.system_instructions", system[:8000])
                span.set_data(
                    "gen_ai.input.messages",
                    _messages_json(system, prompt),
                )
        except Exception as exc:
            log.debug("gen_ai.chat span setup: %s", exc)
        yield span


def finish_chat_span(
    span: Any,
    *,
    result_text: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    model: Optional[str] = None,
    success: bool = True,
) -> None:
    """Attach response + usage attributes to an open gen_ai.chat span."""
    if not _initialized or span is None or isinstance(span, _NoopSpan):
        return
    try:
        if model:
            span.set_data("gen_ai.response.model", model)
        if _include_prompts and result_text is not None:
            span.set_data("gen_ai.output.messages", _output_json(result_text))
        if input_tokens is not None and input_tokens >= 0:
            span.set_data("gen_ai.usage.input_tokens", int(input_tokens))
        if output_tokens is not None and output_tokens >= 0:
            span.set_data("gen_ai.usage.output_tokens", int(output_tokens))
        if total_tokens is not None and total_tokens >= 0:
            span.set_data("gen_ai.usage.total_tokens", int(total_tokens))
        elif input_tokens is not None and output_tokens is not None:
            span.set_data(
                "gen_ai.usage.total_tokens",
                int(input_tokens) + int(output_tokens),
            )
        if not success:
            span.set_data("gen_ai.response.finish_reasons", json.dumps(["error"]))
        else:
            span.set_data("gen_ai.response.finish_reasons", json.dumps(["stop"]))
    except Exception as exc:
        log.debug("finish_chat_span: %s", exc)


@contextmanager
def invoke_agent_span(
    *,
    agent_name: str = "Isaac",
    model: str = "isaac-kernel",
    user_input: str = "",
) -> Iterator[Any]:
    """gen_ai.invoke_agent span for a full kernel process turn."""
    if not _initialized:
        yield _NoopSpan()
        return

    import sentry_sdk

    name = f"invoke_agent {agent_name}"
    with sentry_sdk.start_span(op="gen_ai.invoke_agent", name=name) as span:
        try:
            span.set_data("gen_ai.operation.name", "invoke_agent")
            span.set_data("gen_ai.agent.name", agent_name)
            span.set_data("gen_ai.request.model", model or "isaac-kernel")
            span.set_data("gen_ai.pipeline.name", "isaac-kernel")
            if _include_prompts and user_input:
                span.set_data(
                    "gen_ai.input.messages",
                    json.dumps(
                        [
                            {
                                "role": "user",
                                "parts": [
                                    {
                                        "type": "text",
                                        "content": user_input[:12000],
                                    }
                                ],
                            }
                        ],
                        ensure_ascii=False,
                    ),
                )
        except Exception as exc:
            log.debug("invoke_agent span setup: %s", exc)
        yield span


def finish_agent_span(span: Any, *, result_text: str = "", model: str = "") -> None:
    if not _initialized or span is None or isinstance(span, _NoopSpan):
        return
    try:
        if model:
            span.set_data("gen_ai.request.model", model)
            span.set_data("gen_ai.response.model", model)
        if _include_prompts and result_text is not None:
            span.set_data("gen_ai.output.messages", _output_json(result_text))
    except Exception as exc:
        log.debug("finish_agent_span: %s", exc)


@contextmanager
def execute_tool_span(
    tool_name: str,
    *,
    arguments: Any = None,
    description: str = "",
) -> Iterator[Any]:
    if not _initialized:
        yield _NoopSpan()
        return

    import sentry_sdk

    name = f"execute_tool {tool_name}"
    with sentry_sdk.start_span(op="gen_ai.execute_tool", name=name) as span:
        try:
            span.set_data("gen_ai.operation.name", "execute_tool")
            span.set_data("gen_ai.tool.name", tool_name)
            span.set_data("gen_ai.tool.type", "function")
            if description:
                span.set_data("gen_ai.tool.description", description[:500])
            if arguments is not None and _include_prompts:
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False, default=str)
                span.set_data("gen_ai.tool.call.arguments", arguments[:8000])
        except Exception as exc:
            log.debug("execute_tool span setup: %s", exc)
        yield span


def finish_tool_span(span: Any, result: Any = None) -> None:
    if not _initialized or span is None or isinstance(span, _NoopSpan):
        return
    if result is None or not _include_prompts:
        return
    try:
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        span.set_data("gen_ai.tool.call.result", result[:8000])
    except Exception as exc:
        log.debug("finish_tool_span: %s", exc)


class _NoopSpan:
    def set_data(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_attribute(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_tag(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        return None
