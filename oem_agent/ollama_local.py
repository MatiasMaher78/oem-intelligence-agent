"""Fixed-loopback Ollama transport. No SDK, proxy, redirect, pull or cloud fallback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import http.client
import json
import math
import re
import socket
from threading import Timer
from time import monotonic

MODEL = "qwen3:4b"
MIN_VERSION = (0, 40, 14)  # Reviewed runtime floor; protocol compatibility is not security certification.
ENDPOINT = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)  # Never include response bodies or native exception messages.


@dataclass(frozen=True)
class OllamaLimits:
    max_requests: int = 9
    context_tokens: int = 4096
    output_tokens: int = 512
    request_seconds: float = 45
    session_seconds: float = 180
    question_bytes: int = 4096

    def __post_init__(self):
        for key, lower, upper in (("max_requests", 1, 9), ("context_tokens", 2048, 8192),
                                  ("output_tokens", 1, 1024), ("question_bytes", 1, 4096)):
            value = getattr(self, key)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"Invalid {key}")
        for key, upper in (("request_seconds", 45), ("session_seconds", 180)):
            value = getattr(self, key)
            if type(value) not in (int, float) or not 0 < value <= upper or not math.isfinite(value):
                raise ValueError(f"Invalid {key}")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(content):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def reject(value):
        raise ValueError("nonfinite value")
    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=reject)
        if type(result) is not dict:
            raise ValueError("expected object")
        encode(result)  # Reject escaped lone surrogates and numeric overflow, without logging the body.
        return result
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise OllamaError("invalid_ollama_json") from exc


def _shutdown(connection_socket):
    try:
        connection_socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class OllamaLocalClient:
    def __init__(self, limits: OllamaLimits | None = None):
        if limits is not None and type(limits) is not OllamaLimits:
            raise TypeError("Expected OllamaLimits")
        self.limits = limits or OllamaLimits()
        self._events = []
        self._model_digest = None

    @property
    def events(self):
        return deepcopy(self._events)

    def _request(self, method, path, payload, deadline):
        if (method, path) not in {("GET", "/api/version"), ("GET", "/api/status"),
                                  ("GET", "/api/tags"), ("GET", "/api/ps"),
                                  ("POST", "/api/show"), ("POST", "/api/chat")}:
            raise OllamaError("endpoint_forbidden")
        started = monotonic()
        end = min(deadline, started + self.limits.request_seconds)
        connection, watchdog, status, code, received = None, None, None, None, 0
        try:
            if end <= started:
                raise OllamaError("session_timeout")
            # HTTPConnection to a literal loopback IP ignores HTTP(S)_PROXY and OLLAMA_HOST.
            connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=end - started)
            connection.connect()
            watchdog = Timer(max(0, end - monotonic()), _shutdown, args=(connection.sock,))
            watchdog.daemon = True
            watchdog.start()
            body = encode(payload) if payload is not None else None
            connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            status = response.status
            if status != 200:  # No redirects, authentication, retries or error-body logging.
                raise OllamaError("ollama_http_error")
            chunks = []
            while True:
                if monotonic() >= end:
                    raise OllamaError("request_timeout")
                chunk = response.read1(min(4096, 65537 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > 65536:
                    raise OllamaError("ollama_response_too_large")
            if monotonic() >= end:
                raise OllamaError("request_timeout")
            return decode(b"".join(chunks))
        except OllamaError as exc:
            code = exc.code
            raise
        except (OSError, http.client.HTTPException) as exc:
            code = "request_timeout" if monotonic() >= end or isinstance(exc, TimeoutError) else "ollama_unavailable"
            raise OllamaError(code) from exc
        except BaseException:
            code = "request_interrupted"
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if connection is not None:
                connection.close()
            self._events.append({"method": method, "path": path, "http_status": status,
                                 "elapsed_ms": round((monotonic() - started) * 1000, 3),
                                 "received_bytes": received, "error": code})

    def _local_model(self, deadline):
        state = self._request("GET", "/api/status", None, deadline)
        if type(state.get("cloud")) is not dict or state["cloud"].get("disabled") is not True:
            raise OllamaError("cloud_not_disabled")
        listing = self._request("GET", "/api/tags", None, deadline)
        models = listing.get("models")
        if type(models) is not list:
            raise OllamaError("invalid_model_catalog")
        matches = [model for model in models if type(model) is dict and model.get("name") == MODEL]
        if len(matches) != 1:
            raise OllamaError("local_model_missing")
        model = matches[0]
        digest = model.get("digest")
        if (model.get("remote_host") or model.get("remote_model") or type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or type(model.get("size")) is not int or model["size"] <= 0):
            raise OllamaError("invalid_local_model")
        if self._model_digest is not None and digest != self._model_digest:
            raise OllamaError("model_changed")
        self._model_digest = digest
        return model

    def preflight(self, deadline):
        version = self._request("GET", "/api/version", None, deadline).get("version")
        parsed = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version) if type(version) is str else None
        if parsed is None or tuple(map(int, parsed.groups())) < MIN_VERSION:
            raise OllamaError("unsupported_ollama_version")
        model = self._local_model(deadline)
        shown = self._request("POST", "/api/show", {"model": MODEL}, deadline)
        capabilities = shown.get("capabilities", [])
        details = shown.get("details", {})
        info = shown.get("model_info", {})
        if (type(capabilities) is not list or "tools" not in capabilities or type(details) is not dict
                or details.get("family") != "qwen3" or shown.get("remote_host") or shown.get("remote_model")
                or type(info) is not dict or type(info.get("qwen3.context_length")) is not int
                or info["qwen3.context_length"] < self.limits.context_tokens):
            raise OllamaError("unsupported_local_model")
        return {"endpoint": ENDPOINT, "version": version, "model": MODEL, "digest": self._model_digest,
                "model_size_bytes": model["size"], "cloud_disabled": True,
                "quantization": details.get("quantization_level"), "context_capacity": info["qwen3.context_length"]}

    def chat(self, messages, tools, deadline):
        # Separate byte cap, not a characters-to-tokens estimate. Server token limits remain authoritative.
        context_bytes = len(encode({"messages": messages, "tools": tools}))
        allowance = 4 * (self.limits.context_tokens - self.limits.output_tokens)
        if context_bytes > allowance:
            raise OllamaError("context_budget_exceeded")
        self._local_model(deadline)  # Recheck cloud status and the pinned model before each inference.
        payload = {"model": MODEL, "messages": messages, "tools": tools, "stream": False,
                   "think": False, "truncate": False, "shift": False, "keep_alive": "30s",
                   "options": {"num_ctx": self.limits.context_tokens, "num_predict": self.limits.output_tokens,
                               "temperature": 0, "seed": 0, "num_thread": 4, "num_gpu": 0}}
        frame = self._request("POST", "/api/chat", payload, deadline)
        if frame.get("model") != MODEL or frame.get("remote_host") or frame.get("remote_model") or frame.get("done") is not True:
            raise OllamaError("invalid_chat_response")
        if frame.get("done_reason") != "stop":
            raise OllamaError("generation_incomplete")
        prompt_tokens, output_tokens = frame.get("prompt_eval_count"), frame.get("eval_count")
        if (type(prompt_tokens) is not int or prompt_tokens < 0 or type(output_tokens) is not int or output_tokens < 0
                or prompt_tokens + output_tokens > self.limits.context_tokens or output_tokens >= self.limits.output_tokens):
            raise OllamaError("token_budget_exceeded")
        message = frame.get("message")
        if type(message) is not dict or message.get("role") != "assistant":
            raise OllamaError("invalid_chat_message")
        calls, content = message.get("tool_calls", []), message.get("content", "")
        if type(calls) is not list or type(content) is not str:
            raise OllamaError("invalid_chat_message")
        # Drop intermediate content and all private reasoning fields before returning or auditing.
        content = "" if calls else content
        try:
            content.encode("utf-8")
        except UnicodeError as exc:
            raise OllamaError("invalid_chat_message") from exc
        if any(tag in content.lower() for tag in ("<think", "</think", "<analysis", "</analysis")):
            raise OllamaError("private_reasoning_in_content")
        metrics = {key: value for key, value in frame.items() if key in (
            "prompt_eval_count", "eval_count", "total_duration", "load_duration", "prompt_eval_duration", "eval_duration")
            and type(value) is int and value >= 0}
        return {"content": content, "tool_calls": calls, "metrics": metrics,
                "context_bytes": context_bytes, "thinking_omitted": bool(message.get("thinking"))}

    def resources(self, deadline):
        listing = self._request("GET", "/api/ps", None, deadline)
        models = listing.get("models")
        if type(models) is not list:
            raise OllamaError("invalid_resource_response")
        return [{key: value for key, value in model.items() if key in ("name", "size", "size_vram", "context_length")
                 and type(value) in (str, int)} for model in models if type(model) is dict and model.get("name") == MODEL]
