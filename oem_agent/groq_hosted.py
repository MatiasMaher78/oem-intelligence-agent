"""Groq native function calling over fixed HTTPS; execution stays in the dispatcher."""
from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
import http.client
import math
import os
import re
import ssl
from threading import Timer
from time import monotonic

from .ollama_local import OllamaError, OllamaLimits, encode, decode, _shutdown
from .groq_config import MODEL

ENDPOINT = "https://api.groq.com/openai/v1"
_PATH = "/openai/v1/chat/completions"
_HEADERS = ("x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
            "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens")


class GroqError(OllamaError):
    """Only fixed, nonsecret error codes cross the adapter boundary."""


@dataclass(frozen=True)
class FreeQuota:
    """Recent operator evidence from Console, NOT automatic proof of account billing."""
    checked_at: str
    requests_per_minute: int
    requests_per_day: int
    tokens_per_minute: int
    tokens_per_day: int
    remaining_requests_day: int | None
    remaining_tokens_day: int | None
    plan: str = "free"

    def __post_init__(self):
        if self.plan != "free":
            raise ValueError("Only operator-confirmed Free access is accepted")
        for name, maximum in (("requests_per_minute", 30), ("requests_per_day", 1000),
                              ("tokens_per_minute", 8000), ("tokens_per_day", 200000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid Free quota")
        for remaining, total in ((self.remaining_requests_day, self.requests_per_day),
                                 (self.remaining_tokens_day, self.tokens_per_day)):
            if remaining is not None and (type(remaining) is not int or not 0 <= remaining <= total):
                raise ValueError("Invalid remaining Free quota")
        self.check_fresh()

    def check_fresh(self):
        try:
            checked = datetime.fromisoformat(self.checked_at)
            if checked.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - checked).total_seconds() <= 900:
                raise ValueError()
        except (ValueError, TypeError):
            raise GroqError("free_quota_confirmation_stale") from None


@dataclass(frozen=True)
class LocalBudget:
    """Local batch ceilings, never presented as provider remaining quota."""
    max_requests: int = 9
    max_tokens: int = 24000

    def __post_init__(self):
        for value, maximum in ((self.max_requests, 9), (self.max_tokens, 24000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid local batch budget")


class GroqClient:
    def __init__(self, limits=None, *, free_quota=None, allow_synthetic_transfer=False, local_budget=None):
        if limits is not None and type(limits) is not OllamaLimits:
            raise TypeError("Expected existing OllamaLimits")
        if free_quota is not None and type(free_quota) is not FreeQuota:
            raise TypeError("Expected FreeQuota")
        if local_budget is not None and type(local_budget) is not LocalBudget:
            raise TypeError("Expected LocalBudget")
        if type(allow_synthetic_transfer) is not bool:
            raise TypeError("Expected explicit transfer authorization")
        self.limits = limits or OllamaLimits()
        self._quota = free_quota
        self._budget = local_budget or LocalBudget()
        self._authorized = allow_synthetic_transfer
        self._events = []
        self._window = []
        self._used_requests = self._used_tokens = self._reported_tokens = 0
        self._prompt_basis = None
        self._remote_remaining_requests = self._remote_remaining_tokens = None

    @property
    def events(self):
        return deepcopy(self._events)

    @property
    def budget_status(self):
        return {"limits": asdict(self._budget), "used_requests": self._used_requests,
                "charged_tokens": self._used_tokens, "reported_tokens": self._reported_tokens,
                "estimated_unreported_tokens": self._used_tokens-self._reported_tokens,
                "remaining_requests": self._budget.max_requests-self._used_requests,
                "remaining_tokens": self._budget.max_tokens-self._used_tokens}

    def _check_daily_budget(self, requests=0, tokens=0):
        if (self._used_requests+requests > self._budget.max_requests
                or self._used_tokens+tokens > self._budget.max_tokens):
            raise GroqError("local_budget_exhausted")
        if (self._used_requests+requests > self._quota.requests_per_day
                or self._used_tokens+tokens > self._quota.tokens_per_day
                or self._quota.remaining_requests_day is not None
                and self._used_requests+requests > self._quota.remaining_requests_day
                or self._quota.remaining_tokens_day is not None
                and self._used_tokens+tokens > self._quota.remaining_tokens_day):
            raise GroqError("free_quota_exhausted")

    def _request(self, method, path, payload, deadline):
        if (method, path) not in {("GET", "/openai/v1/models"), ("POST", _PATH)}:
            raise GroqError("endpoint_forbidden")
        if method == "POST" and not self._authorized:
            raise GroqError("synthetic_transfer_not_authorized")
        started = monotonic()
        end = min(deadline, started + self.limits.request_seconds)
        connection = watchdog = None
        status, code, received, rate = None, None, 0, {}
        try:
            if end <= started:
                raise GroqError("session_timeout")
            key = os.environ.get("GROQ_API_KEY")  # Loader is explicit; transport never persists the key.
            if not key:
                raise GroqError("groq_api_key_missing")
            if not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key):
                raise GroqError("groq_api_key_invalid")
            # Literal trusted host; ignores proxy/environment routing and never follows redirects.
            connection = http.client.HTTPSConnection("api.groq.com", 443, timeout=end-started,
                                                      context=ssl.create_default_context())
            connection.connect()
            watchdog = Timer(max(0, end-monotonic()), _shutdown, args=(connection.sock,))
            watchdog.daemon = True
            watchdog.start()
            connection.request(method, path, body=encode(payload) if payload is not None else None,
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
            response = connection.getresponse()
            status = response.status
            for name in _HEADERS:
                value = response.getheader(name)
                if value is not None:
                    if type(value) is not str or re.fullmatch(r"\d{1,12}", value) is None:
                        raise GroqError("invalid_quota_headers")
                    rate[name] = int(value)
            if status != 200:
                raise GroqError({401: "groq_auth_failed", 403: "groq_access_denied", 404: "groq_model_unavailable",
                                 429: "groq_rate_limited"}.get(status, "groq_http_error"))
            if method == "POST" and self._quota is not None:
                for header, expected in (("x-ratelimit-limit-requests", self._quota.requests_per_day),
                                         ("x-ratelimit-limit-tokens", self._quota.tokens_per_minute)):
                    if header in rate and rate[header] != expected:
                        raise GroqError("unexpected_account_limits")
            chunks = []
            while True:
                if monotonic() >= end:
                    raise GroqError("request_timeout")
                chunk = response.read1(min(4096, 65537-received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > 65536:
                    raise GroqError("groq_response_too_large")
            if monotonic() >= end:
                raise GroqError("request_timeout")
            try:
                frame = decode(b"".join(chunks))
            except OllamaError:
                raise GroqError("invalid_groq_json") from None
            if "x-ratelimit-remaining-requests" in rate:
                self._remote_remaining_requests = rate["x-ratelimit-remaining-requests"]
            if "x-ratelimit-remaining-tokens" in rate:
                self._remote_remaining_tokens = rate["x-ratelimit-remaining-tokens"]
            return frame
        except GroqError as exc:
            code = exc.code
            raise
        except (OSError, http.client.HTTPException) as exc:
            code = "request_timeout" if monotonic() >= end or isinstance(exc, TimeoutError) else "groq_unavailable"
            raise GroqError(code) from None
        except BaseException:
            code = "request_interrupted"
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if connection is not None:
                connection.close()
            self._events.append({"method": method, "path": path, "http_status": status,
                                 "elapsed_ms": round((monotonic()-started)*1000, 3),
                                 "received_bytes": received, "error": code, "quota_headers": rate})

    def availability(self, deadline):
        if os.environ.get("GROQ_MODEL", MODEL) != MODEL:
            raise GroqError("groq_model_not_allowed")
        frame = self._request("GET", "/openai/v1/models", None, deadline)
        data = frame.get("data")
        if type(data) is not list:
            raise GroqError("invalid_model_catalog")
        matches = [m for m in data if type(m) is dict and m.get("id") == MODEL]
        if len(matches) != 1 or matches[0].get("active") is not True:
            raise GroqError("groq_model_unavailable")
        context = matches[0].get("context_window")
        if type(context) is not int or context < self.limits.context_tokens:
            raise GroqError("unsupported_model_context")
        return {"provider": "groq", "endpoint": ENDPOINT, "model": MODEL, "context_capacity": context,
                "active": True, "free_plan_api_verified": False}

    def preflight(self, deadline):
        if self._quota is None:
            raise GroqError("free_quota_confirmation_required")
        self._quota.check_fresh()
        self._check_daily_budget(requests=1, tokens=1)
        info = self.availability(deadline)
        info["operator_confirmed_free_quota"] = asdict(self._quota)
        info["local_batch_budget"] = self.budget_status
        return info

    def _reserve(self, amount):
        if self._quota is None:
            raise GroqError("free_quota_confirmation_required")
        self._quota.check_fresh()
        self._check_daily_budget(requests=1, tokens=amount)
        now = monotonic()
        self._window = [(t, n) for t, n in self._window if now-t < 60]
        if (len(self._window) >= self._quota.requests_per_minute
                or sum(n for _, n in self._window)+amount > self._quota.tokens_per_minute
                or self._remote_remaining_requests is not None and self._remote_remaining_requests < 1
                or self._remote_remaining_tokens is not None and self._remote_remaining_tokens < amount):
            raise GroqError("free_quota_exhausted")
        self._window.append((now, amount))
        self._used_requests += 1
        self._used_tokens += amount

    @staticmethod
    def _messages(messages):
        translated = []
        for message in messages:
            role = message["role"]
            item = {"role": role, "content": message["content"]}
            if role == "assistant" and message.get("tool_calls"):
                item["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                    "name": call["function"]["name"], "arguments": encode(call["function"]["arguments"]).decode("utf-8")}}
                    for call in message["tool_calls"]]
            if role == "tool":
                item["tool_call_id"] = message["tool_call_id"]
            translated.append(item)
        return translated

    def _estimate_prompt(self, messages, tools):
        """Use a measured identical prefix; newly appended bytes remain an estimate.

        This is not a tokenizer or reported consumption. Changed prefixes/tools
        fall back to the conservative full encoded-byte estimate.
        """
        size = len(encode({"messages": messages, "tools": tools}))
        identities = tuple(sha256(encode(message)).digest() for message in messages)
        tools_digest = sha256(encode(tools)).digest()
        previous = self._prompt_basis
        if (previous is not None and previous["tools"] == tools_digest
                and len(identities) >= len(previous["messages"])
                and identities[:len(previous["messages"])] == previous["messages"]):
            estimate = previous["prompt_tokens"] + max(0, size-previous["bytes"])
            basis = "reported_prompt_plus_new_bytes"
        else:
            estimate, basis = size, "full_bytes_estimate"
        return estimate, basis, {"messages": identities, "tools": tools_digest, "bytes": size}

    def chat(self, messages, tools, deadline):
        if not self._authorized:
            raise GroqError("synthetic_transfer_not_authorized")
        context_bytes = len(encode({"messages": messages, "tools": tools}))
        if context_bytes > 4*(self.limits.context_tokens-self.limits.output_tokens):
            raise GroqError("context_budget_exceeded")
        self.preflight(deadline)  # Active model and recent Free confirmation before every inference.
        translated = self._messages(messages)
        estimated_prompt, reservation_basis, prompt_basis = self._estimate_prompt(translated, tools)
        payload = {"model": MODEL, "messages": translated, "tools": tools,
                   "tool_choice": "auto", "stream": False, "n": 1, "temperature": 0,
                   "max_completion_tokens": self.limits.output_tokens, "reasoning_effort": "none",
                   "reasoning_format": "hidden", "service_tier": "on_demand"}
        # Keep estimated reservations separate from actual provider usage.
        reserved = estimated_prompt + self.limits.output_tokens + 512
        self._reserve(reserved)
        frame = self._request("POST", _PATH, payload, deadline)
        if frame.get("model") != MODEL or frame.get("object") != "chat.completion":
            raise GroqError("invalid_chat_response")
        choices, usage = frame.get("choices"), frame.get("usage")
        if type(choices) is not list or len(choices) != 1 or type(usage) is not dict:
            raise GroqError("invalid_chat_response")
        choice = choices[0]
        if type(choice) is not dict or type(choice.get("index")) is not int or choice["index"] != 0:
            raise GroqError("generation_incomplete")
        prompt, output, total = (usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
        if any(type(v) is not int or v < 0 for v in (prompt, output, total)) or total != prompt+output:
            raise GroqError("token_budget_exceeded")
        # Valid reported usage is real consumption even when the response later fails.
        self._used_tokens += total-reserved
        self._reported_tokens += total
        self._window[-1] = (self._window[-1][0], total)
        if total > self.limits.context_tokens or output >= self.limits.output_tokens:
            raise GroqError("token_budget_exceeded")
        if total > reserved:
            raise GroqError("quota_estimate_exceeded")
        if choice.get("finish_reason") not in ("stop", "tool_calls"):
            raise GroqError("generation_incomplete")
        message = choice.get("message")
        if type(message) is not dict or message.get("role") != "assistant" or message.get("refusal"):
            raise GroqError("invalid_chat_message")
        calls, content = message.get("tool_calls", []), message.get("content")
        if type(calls) is not list or (choice["finish_reason"] == "tool_calls") != bool(calls):
            raise GroqError("invalid_chat_message")
        normalized = []
        ids = set()
        for call in calls:
            function = call.get("function") if type(call) is dict else None
            function = function if type(function) is dict else {}
            call_id = call.get("id") if type(call) is dict else None
            arguments = function.get("arguments")
            try:
                arguments = decode(arguments) if type(arguments) is str else None
            except OllamaError:
                arguments = None  # Do not log malformed raw text; dispatcher counts/rejects the attempt.
            valid_id = type(call_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call_id) is not None and call_id not in ids
            if not valid_id or type(call) is not dict or call.get("type") != "function":
                arguments = None
            if valid_id:
                ids.add(call_id)
            normalized.append({"id": call_id if valid_id else "invalid", "function": {
                "name": function.get("name"), "arguments": arguments}})
        content = "" if calls else content
        if type(content) is not str or any(t in content.lower() for t in ("<think", "</think", "<analysis", "</analysis")):
            raise GroqError("private_or_invalid_final_content")
        self._prompt_basis = {**prompt_basis, "prompt_tokens": prompt}
        metrics = {"prompt_eval_count": prompt, "eval_count": output,
                   "reported_total_tokens": total, "estimated_prompt_tokens": estimated_prompt,
                   "reserved_tokens": reserved, "reservation_basis": reservation_basis}
        for name in ("prompt_time", "completion_time", "total_time", "queue_time"):
            value = usage.get(name)
            if type(value) in (float, int) and math.isfinite(value) and value >= 0:
                metrics[name] = value
        if metrics.get("completion_time", 0) > 0:
            metrics["generation_tokens_per_second"] = output/metrics["completion_time"]
        return {"content": content, "tool_calls": normalized, "metrics": metrics,
                "context_bytes": context_bytes, "thinking_omitted": bool(message.get("reasoning") or message.get("thinking"))}

    def resources(self, deadline):
        return []  # Hosted model RAM/CPU is not exposed and is never inferred from local process RSS.
