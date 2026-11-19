"""EP v6 Unified Local Model Router with per-scope routing, circuit breaker, and telemetry."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
        scope_modes: dict[str, str] | None = None,
        telemetry_path: Path | None = None,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if default_scope not in scopes or not scopes[default_scope]:
            raise ValueError("default_scope_providers_required")
        self.scopes = scopes
        self.default_scope = default_scope
        # Supported modes per scope: "failover" | "parallel_race" | "round_robin"
        self.scope_modes = dict(scope_modes or {})
        self.telemetry_path = telemetry_path
        self._opener = opener or urllib.request.urlopen
        self._clock = clock
        self._lock = threading.Lock()
        self._breakers: dict[str, BreakerState] = {}
        self._metrics: dict[str, dict[str, int]] = {}
        self._rr_counters: dict[str, int] = {}

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

    def _call_one(self, ep: ProviderEndpoint, payload: dict[str, Any], scope: str) -> tuple[dict[str, Any], str]:
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
            self._record_success(ep, scope, latency_ms, parsed.get("usage") or {})
            return parsed, ep.name
        except urllib.error.HTTPError as exc:
            code = f"http_{exc.code}"
            self._record_failure(ep, scope, code, int((self._clock() - t0) * 1000))
            raise RuntimeError(f"{ep.name}:{code}") from exc
        except Exception as exc:
            code = type(exc).__name__
            self._record_failure(ep, scope, code, int((self._clock() - t0) * 1000))
            raise RuntimeError(f"{ep.name}:{code}") from exc

    def chat_completions(self, payload: dict[str, Any], *, scope: str | None = None, mode: str | None = None) -> tuple[dict[str, Any], str]:
        resolved_scope = scope if scope in self.scopes else self.default_scope
        chain = list(self.scopes[resolved_scope])
        routing_mode = mode or self.scope_modes.get(resolved_scope, "failover")

        with self._lock:
            available = [ep for ep in chain if self._is_available(ep)]
            if routing_mode == "round_robin" and len(available) > 1:
                idx = self._rr_counters.get(resolved_scope, 0) % len(available)
                self._rr_counters[resolved_scope] = idx + 1
                available = available[idx:] + available[:idx]

        if not available:
            raise RuntimeError("all_providers_failed:" + ";".join(f"{ep.name}:circuit_open" for ep in chain))

        if routing_mode == "parallel_race" and len(available) > 1:
            errors: list[str] = []
            pool = ThreadPoolExecutor(max_workers=len(available))
            try:
                futs = {pool.submit(self._call_one, ep, payload, resolved_scope): ep for ep in available}
                pending = set(futs.keys())
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for fut in done:
                        try:
                            return fut.result()
                        except Exception as exc:
                            errors.append(str(exc))
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError("all_providers_failed:" + ";".join(errors))

        errors = []
        for ep in available:
            try:
                return self._call_one(ep, payload, resolved_scope)
            except Exception as exc:
                errors.append(str(exc))

        raise RuntimeError("all_providers_failed:" + ";".join(errors))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scopes": {k: [ep.name for ep in v] for k, v in self.scopes.items()},
                "scope_modes": dict(self.scope_modes),
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
            if self.path in ("/health", "/metrics"):
                self._send(200, {"status": "healthy", "router": router.snapshot()})
            elif self.path.rstrip("/") == "/v1/models":
                seen = []
                for chain in router.scopes.values():
                    for ep in chain:
                        if ep.model not in seen:
                            seen.append(ep.model)
                self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in seen]})
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


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_router_from_data_root(data_root: str | Path) -> ModelRouter:
    """Build a unified ModelRouter from EP_DATA_ROOT/config/runtime-settings.json and profiles/evolving-profile-api.env."""
    root = Path(data_root).expanduser()
    settings_path = root / "config" / "runtime-settings.json"
    env = _parse_env(root / "profiles" / "evolving-profile-api.env")

    default_chain: list[ProviderEndpoint] = []
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        providers = settings.get("providers") or {}
        primary = providers.get("primary") or {}
        if primary.get("base_url") and primary.get("model") and primary.get("api_key"):
            default_chain.append(
                ProviderEndpoint("settings_primary", primary["base_url"], primary["api_key"], primary["model"])
            )
        for idx, fb in enumerate(providers.get("fallbacks") or [], start=1):
            if isinstance(fb, dict) and fb.get("base_url") and fb.get("model") and fb.get("api_key"):
                default_chain.append(
                    ProviderEndpoint(f"settings_fallback_{idx}", fb["base_url"], fb["api_key"], fb["model"])
                )

    api_chain: list[ProviderEndpoint] = []
    api_url = env.get("EVOLVING_PROFILE_API_LLM_BASE_URL")
    api_key = env.get("EVOLVING_PROFILE_API_LLM_API_KEY")
    api_model = env.get("EVOLVING_PROFILE_API_LLM_MODEL")
    if api_url and api_key and api_model:
        api_chain.append(ProviderEndpoint("api_primary", api_url, api_key, api_model))
    if env.get("EVOLVING_PROFILE_API_LLM_1_MODEL") and api_url and api_key:
        api_chain.append(
            ProviderEndpoint(
                "api_fallback_1",
                env.get("EVOLVING_PROFILE_API_LLM_1_BASE_URL") or api_url,
                env.get("EVOLVING_PROFILE_API_LLM_1_API_KEY") or api_key,
                env["EVOLVING_PROFILE_API_LLM_1_MODEL"],
            )
        )

    reflect_chain: list[ProviderEndpoint] = []
    r_model = env.get("EVOLVING_PROFILE_API_REFLECT_LLM_MODEL")
    r_url = env.get("EVOLVING_PROFILE_API_REFLECT_LLM_BASE_URL") or api_url
    r_key = env.get("EVOLVING_PROFILE_API_REFLECT_LLM_API_KEY") or api_key
    if r_model and r_url and r_key:
        reflect_chain.append(ProviderEndpoint("reflect_primary", r_url, r_key, r_model, timeout=150.0))
    reflect_chain.extend(ep for ep in api_chain if ep.model != r_model)

    if not default_chain:
        default_chain = list(api_chain)
    if not default_chain:
        raise ValueError("no_configured_providers_found")

    scopes: dict[str, list[ProviderEndpoint]] = {
        "default": default_chain,
        "context": default_chain,
        "retain": api_chain or default_chain,
        "consolidation": api_chain or default_chain,
        "reflect": reflect_chain or api_chain or default_chain,
        "guidance": api_chain or default_chain,
    }
    scope_modes = {
        "default": "parallel_race",
        "context": "parallel_race",
        "retain": "round_robin",
        "consolidation": "round_robin",
        "reflect": "parallel_race",
        "guidance": "round_robin",
    }
    return ModelRouter(scopes, scope_modes=scope_modes, telemetry_path=root / "logs" / "v6-model-router-telemetry.jsonl")


def main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="EP v6 Unified Local Model Router")
    parser.add_argument("--data-root", default=os.environ.get("EP_DATA_ROOT") or str(Path.home() / ".evolving-profile"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12095)
    args = parser.parse_args()
    router = load_router_from_data_root(args.data_root)
    server = create_router_server(router, host=args.host, port=args.port)
    print(json.dumps({"status": "started", "host": args.host, "port": args.port, "scopes": router.snapshot()["scopes"]}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
