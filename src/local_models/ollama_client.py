"""Bounded asynchronous client for the documented local Ollama API."""

from __future__ import annotations

from collections.abc import AsyncIterator
import ipaddress
import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from .contracts import InstalledModel
from .identifiers import parse_model_reference

_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_STREAM_LINE = 256 * 1024


class OllamaClientError(RuntimeError):
    """Stable client failure. The message is safe to return to API callers."""

    def __init__(self, code: str, *, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def configured_ollama_host() -> str:
    return os.getenv("LUMENA_OLLAMA_HOST", os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")


def validate_ollama_host(host: str, *, allow_remote: bool = False) -> str:
    try:
        parsed = urlsplit(host)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError
        hostname = parsed.hostname.lower()
        is_local = hostname == "localhost"
        try:
            is_local = is_local or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            pass
        if not is_local and not allow_remote:
            raise OllamaClientError("ollama_remote_host_forbidden")
        return host.rstrip("/")
    except OllamaClientError:
        raise
    except (TypeError, ValueError):
        raise OllamaClientError("ollama_host_invalid") from None


class OllamaClient:
    def __init__(
        self,
        host: str | None = None,
        *,
        allow_remote: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if allow_remote is None:
            allow_remote = os.getenv("LUMENA_OLLAMA_ALLOW_REMOTE", "0").lower() in {"1", "true", "yes", "on"}
        self.host = validate_ollama_host(host or configured_ollama_host(), allow_remote=allow_remote)
        self._transport = transport

    def _client(self, *, pull: bool = False) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            connect=float(os.getenv("LUMENA_OLLAMA_CONNECT_TIMEOUT", "5")),
            read=float(
                os.getenv(
                    "LUMENA_OLLAMA_PULL_TIMEOUT" if pull else "LUMENA_OLLAMA_READ_TIMEOUT", "900" if pull else "20"
                )
            ),
            write=20.0,
            pool=10.0,
        )
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
            trust_env=False,
        )

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with self._client() as client:
                if method == "GET":
                    response = await client.get(self._url(path), **kwargs)
                elif method == "POST":
                    response = await client.post(self._url(path), **kwargs)
                else:
                    response = await client.request(method, self._url(path), **kwargs)
                return await self._json(response)
        except httpx.TimeoutException:
            raise OllamaClientError("ollama_timeout") from None
        except httpx.HTTPError:
            raise OllamaClientError("ollama_unreachable") from None

    @staticmethod
    async def _json(response: httpx.Response) -> dict[str, Any]:
        if response.is_redirect:
            raise OllamaClientError("ollama_redirect_refused", status_code=response.status_code)
        if response.status_code >= 400:
            raise OllamaClientError("ollama_http_error", status_code=response.status_code)
        content = await response.aread()
        if len(content) > _MAX_JSON_BYTES:
            raise OllamaClientError("ollama_response_too_large")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OllamaClientError("ollama_json_invalid") from None
        if type(payload) is not dict:
            raise OllamaClientError("ollama_json_invalid")
        return payload

    async def health(self) -> dict[str, Any]:
        try:
            payload = await self._request_json("GET", "/api/tags")
            return {"available": True, "model_count": len(payload.get("models", []))}
        except (httpx.HTTPError, OllamaClientError) as exc:
            return {"available": False, "error_code": getattr(exc, "code", "ollama_unreachable")}

    async def version(self) -> str | None:
        payload = await self._request_json("GET", "/api/version")
        version = payload.get("version")
        return version if type(version) is str and len(version) <= 64 else None

    async def list_installed(self) -> list[InstalledModel]:
        payload = await self._request_json("GET", "/api/tags")
        models = payload.get("models")
        if type(models) is not list or len(models) > 10000:
            raise OllamaClientError("ollama_models_invalid")
        result: list[InstalledModel] = []
        for item in models:
            if type(item) is not dict or type(item.get("name")) is not str:
                continue
            try:
                ref = parse_model_reference(item["name"], "ollama")
            except ValueError:
                continue
            details = item.get("details") if type(item.get("details")) is dict else {}
            result.append(
                InstalledModel(
                    reference=ref,
                    digest=str(item.get("digest") or "")[:256],
                    size_bytes=item.get("size") if type(item.get("size")) is int and item["size"] >= 0 else None,
                    modified_at=item.get("modified_at") if type(item.get("modified_at")) is str else None,
                    family=str(details.get("family") or "")[:128],
                    parameter_count=None,
                    quantization=str(details.get("quantization_level") or "")[:64],
                )
            )
        return result

    async def list_running(self) -> list[dict[str, Any]]:
        payload = await self._request_json("GET", "/api/ps")
        models = payload.get("models")
        if type(models) is not list:
            raise OllamaClientError("ollama_models_invalid")
        return [item for item in models[:10000] if type(item) is dict and type(item.get("name")) is str]

    async def show(self, reference: str) -> dict[str, Any]:
        ref = parse_model_reference(reference)
        return await self._request_json("POST", "/api/show", json={"model": ref.pull_reference})

    async def pull(self, reference: str) -> AsyncIterator[dict[str, Any]]:
        ref = parse_model_reference(reference)
        last_percent = 0.0
        try:
            async with self._client(pull=True) as client:
                async with client.stream(
                    "POST", self._url("/api/pull"), json={"model": ref.pull_reference, "stream": True}
                ) as response:
                    if response.is_redirect:
                        raise OllamaClientError("ollama_redirect_refused", status_code=response.status_code)
                    if response.status_code >= 400:
                        raise OllamaClientError("ollama_http_error", status_code=response.status_code)
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if len(line.encode("utf-8", errors="ignore")) > _MAX_STREAM_LINE:
                            raise OllamaClientError("ollama_stream_line_too_large")
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            raise OllamaClientError("ollama_stream_json_invalid") from None
                        if type(item) is not dict:
                            raise OllamaClientError("ollama_stream_json_invalid")
                        if item.get("error"):
                            raise OllamaClientError("ollama_pull_failed")
                        total = item.get("total") if type(item.get("total")) is int else None
                        completed = item.get("completed") if type(item.get("completed")) is int else None
                        percent = last_percent
                        if total and completed is not None and total > 0:
                            percent = min(100.0, max(last_percent, round(completed / total * 100, 2)))
                        last_percent = percent
                        yield {
                            "status": str(item.get("status") or "")[:160],
                            "completed_bytes": completed,
                            "total_bytes": total,
                            "percent": percent,
                            "done": item.get("status") == "success",
                        }
        except httpx.TimeoutException:
            raise OllamaClientError("ollama_timeout") from None
        except httpx.HTTPError:
            raise OllamaClientError("ollama_unreachable") from None

    async def delete(self, reference: str) -> None:
        ref = parse_model_reference(reference)
        await self._request_json("DELETE", "/api/delete", json={"model": ref.pull_reference})

    async def unload(self, reference: str) -> None:
        ref = parse_model_reference(reference)
        await self._request_json(
            "POST", "/api/generate", json={"model": ref.pull_reference, "keep_alive": 0, "stream": False}
        )

    async def generate_canary(self, reference: str, *, max_tokens: int = 8) -> str:
        """Run one bounded text canary. Its content is never shown as chat output."""
        ref = parse_model_reference(reference)
        max_tokens = max(1, min(int(max_tokens), 32))
        payload = await self._request_json(
            "POST",
            "/api/generate",
            json={
                "model": ref.pull_reference,
                "prompt": "Reply with OK.",
                "stream": False,
                "keep_alive": 0,
                "options": {"num_predict": max_tokens, "temperature": 0},
            },
        )
        response = payload.get("response")
        if type(response) is not str or not response.strip():
            raise OllamaClientError("ollama_canary_empty")
        return response.strip()[:256]
