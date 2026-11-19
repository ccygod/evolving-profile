"""EP v6 Unified Local Model Router with per-scope routing, circuit breaker, and telemetry."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


@dataclass
class ProviderEndpoint:
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0


@dataclass
class BreakerState:
    state: str = "closed"  # closed | open | half_open
    consecutive_failures: int = 0
    opened_at: float = 0.0
    last_failure_code: str = ""


class ModelRouter:
    """Routes OpenAI-compatible chat completions across per-scope provider chains with circuit breaking."""

    def __init__(
        self,
        scopes: dict[str, list[ProviderEndpoint]],
        *,
        default_scope: str = "default",
        telemetry_path: Path | None = None,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if default_scope not in scopes or not scopes[default_scope]:
            raise ValueError("default_scope_providers_required")
        self.scopes = scopes
        self.default_scope = default_scope
        self.telemetry_path = telemetry_path
        self._opener = opener or urllib.request.urlopen
        self._clock = clock
        self._lock = threading.Lock()
        self._breakers: dict[str, BreakerState] = {}
        self._metrics: dict[str, dict[str, int]] = {}

    def _breaker(self, name: str) -> BreakerState:
        return self._breakers.setdefault(name, BreakerState())

    def _is_available(self, ep: ProviderEndpoint) -> bool:
        b = self._breaker(ep.name)
        if b.state == "closed":
            return True
        if b.state == "open" and (self._clock() - b.opened_at) >= ep.cooldown_seconds:
            b.state = "half_open"
            return True
        return b.state == "half_open"

    def _record_success(self, ep: ProviderEndpoint, scope: str, latency_ms: int, usage: dict) -> None:
        with self._lock:
            b = self._breaker(ep.name)
            b.state = "closed"
            b.consecutive_failures = 0
            b.last_failure_code = ""
            m = self._metrics.setdefault(scope, {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["input_tokens"] += int(usage.get("prompt_tokens") or 0)
            m["output_tokens"] += int(usage.get("completion_tokens") or 0)
        self._append_telemetry(scope, ep.name, ep.model, "ok", latency_ms, usage)

    def _record_failure(self, ep: ProviderEndpoint, scope: str, code: str, latency_ms: int) -> None:
        with self._lock:
            b = self._breaker(ep.name)
            b.consecutive_failures += 1
            b.last_failure_code = code
            if b.state == "half_open" or b.consecutive_failures >= ep.failure_threshold:
                b.state = "open"
                b.opened_at = self._clock()
            m = self._metrics.setdefault(scope, {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0})
            m["errors"] += 1
        self._append_telemetry(scope, ep.name, ep.model, code, latency_ms, {})

    def _append_telemetry(self, scope: str, provider: str, model: str, status: str, latency_ms: int, usage: dict) -> None:
        if not self.telemetry_path:
            return
        self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": round(self._clock(), 3),
            "scope": scope,
            "provider": provider,
            "model": model,
            "status": status,
            "latency_ms": latency_ms,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
        with self.telemetry_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def chat_completions(self, payload: dict[str, Any], *, scope: str | None = None) -> tuple[dict[str, Any], str]:
        resolved_scope = scope if scope in self.scopes else self.default_scope
        chain = self.scopes[resolved_scope]
        errors: list[str] = []

        for ep in chain:
            with self._lock:
                allowed = self._is_available(ep)
            if not allowed:
                errors.append(f"{ep.name}:circuit_open")
                continue

            req_body = {**payload, "model": ep.model}
            url = ep.base_url.rstrip("/") + "/chat/completions"
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {ep.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            t0 = self._clock()
            try:
                with self._opener(req, timeout=ep.timeout) as resp:
                    raw = resp.read()
                parsed = json.loads(raw)
                latency_ms = int((self._clock() - t0) * 1000)
                self._record_success(ep, resolved_scope, latency_ms, parsed.get("usage") or {})
                return parsed, ep.name
            except urllib.error.HTTPError as exc:
                code = f"http_{exc.code}"
                self._record_failure(ep, resolved_scope, code, int((self._clock() - t0) * 1000))
                errors.append(f"{ep.name}:{code}")
            except Exception as exc:
                code = type(exc).__name__
                self._record_failure(ep, resolved_scope, code, int((self._clock() - t0) * 1000))
                errors.append(f"{ep.name}:{code}")

        raise RuntimeError("all_providers_failed:" + ";".join(errors))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scopes": {k: [ep.name for ep in v] for k, v in self.scopes.items()},
                "breakers": {
                    k: {
                        "state": b.state,
                        "consecutive_failures": b.consecutive_failures,
                        "last_failure_code": b.last_failure_code,
                    }
                    for k, b in self._breakers.items()
                },
                "metrics": dict(self._metrics),
            }


def create_router_server(router: ModelRouter, host: str = "127.0.0.1", port: int = 12095) -> ThreadingHTTPServer:
    """Create a local OpenAI-compatible HTTP proxy backed by ModelRouter."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "healthy", "router": router.snapshot()})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._send(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            scope = self.headers.get("X-EP-Scope") or payload.pop("ep_scope", None)
            try:
                result, provider_name = router.chat_completions(payload, scope=scope)
                result.setdefault("_ep_router", {"provider": provider_name, "scope": scope or router.default_scope})
                self._send(200, result)
            except Exception as exc:
                self._send(502, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
