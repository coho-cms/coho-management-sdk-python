"""The HTTP layer: one place that knows about bearer tokens, problem documents and ETags.

Everything above this module speaks in paths relative to the BFF and gets back
either a parsed body or a `CohoError`. Nothing above it imports httpx.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ._version import __version__
from .errors import CohoError, TransportError, error_for_response

TokenProvider = Callable[[], str | None]
"""Returns the access token to send, or ``None`` for an anonymous request."""

DEFAULT_TIMEOUT = 30.0


def segment(value: str) -> str:
    """URL-encode one path segment.

    Branch names contain slashes (``feature/x``), and authoring.yaml requires them
    encoded (``feature%2Fx``). Encoding everything, including ids, costs nothing.
    """
    return quote(value, safe="")


@dataclass(slots=True)
class Response:
    """A successful response, with the headers the SDK cares about."""

    status: int
    body: Any
    etag: str | None
    headers: httpx.Headers
    raw: httpx.Response

    @property
    def location(self) -> str | None:
        value = self.headers.get("location")
        return str(value) if value is not None else None


class Transport:
    """A thin, synchronous client over one BFF base URL.

    Args:
        base_url: the BFF, e.g. ``https://staging.coho.example``.
        token_provider: called before every request for the bearer token.
        timeout: seconds; ``None`` for no timeout.
        client: an ``httpx.Client`` to use instead of building one — tests use this.
    """

    def __init__(
        self,
        base_url: str,
        token_provider: TokenProvider | None = None,
        *,
        timeout: float | None = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token_provider = token_provider or (lambda: None)
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._user_agent = user_agent or f"coho-management-sdk/{__version__}"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the one method everything else uses ---------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        if_match: str | None = None,
        follow_redirects: bool = False,
        stream: bool = False,
    ) -> Response:
        """Send one request and return a `Response`, or raise a `CohoError`.

        ``params`` entries whose value is ``None`` are dropped, so callers can pass
        optional query parameters without filtering.
        """
        request_headers = self._headers(headers)
        if if_match is not None:
            request_headers["If-Match"] = if_match
        clean_params = {k: _param(v) for k, v in (params or {}).items() if v is not None}
        url = self.base_url + path
        try:
            if stream:
                req = self._client.build_request(
                    method, url, json=json_body, params=clean_params, headers=request_headers
                )
                raw = self._client.send(req, stream=True, follow_redirects=follow_redirects)
            else:
                raw = self._client.request(
                    method,
                    url,
                    json=json_body,
                    params=clean_params,
                    headers=request_headers,
                    follow_redirects=follow_redirects,
                )
        except httpx.HTTPError as exc:
            raise TransportError(f"{method} {url}: {exc}") from exc

        if raw.status_code >= 400:
            if stream:
                raw.read()
            raise error_for_response(raw)

        body: Any = None
        if not stream and raw.content:
            content_type = raw.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    body = raw.json()
                except ValueError as exc:
                    raise TransportError(
                        f"{method} {path}: response was not valid JSON", status=raw.status_code
                    ) from exc
            else:
                body = raw.text
        return Response(
            status=raw.status_code,
            body=body,
            etag=raw.headers.get("etag"),
            headers=raw.headers,
            raw=raw,
        )

    # -- conveniences ------------------------------------------------------------

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any | None = None, **kw: Any) -> Response:
        return self.request("POST", path, json_body=json_body, **kw)

    def put(self, path: str, json_body: Any | None = None, **kw: Any) -> Response:
        return self.request("PUT", path, json_body=json_body, **kw)

    def patch(self, path: str, json_body: Any | None = None, **kw: Any) -> Response:
        return self.request("PATCH", path, json_body=json_body, **kw)

    def delete(self, path: str, **kw: Any) -> Response:
        return self.request("DELETE", path, **kw)

    def stream_bytes(self, response: Response, chunk_size: int = 65536) -> Iterator[bytes]:
        """Iterate a streaming response's body, then close it."""
        try:
            yield from response.raw.iter_bytes(chunk_size)
        finally:
            response.raw.close()

    # -- internals -------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, application/problem+json",
            "User-Agent": self._user_agent,
        }
        token = self._token_provider()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra:
            headers.update(extra)
        return headers


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ",".join(str(v) for v in value)
    return str(value)


def pretty(body: Any) -> str:
    """JSON for humans, used by the CLI's ``--output json`` and by error printing."""
    return json.dumps(body, indent=2, sort_keys=False, ensure_ascii=False)


__all__ = ["Transport", "Response", "TokenProvider", "CohoError", "segment", "pretty"]
