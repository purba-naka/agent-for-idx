from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.idx_client import IDXClient


class ResponsePalsu:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class SessionPalsu:
    def __init__(self, responses: list[ResponsePalsu]) -> None:
        self.responses = responses
        self.calls = 0

    async def get(self, *_args: Any, **_kwargs: Any) -> ResponsePalsu:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class IDXClientRetryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = IDXClient(Settings())
        self.params = {"pageSize": 500}

    async def test_retries_transient_503_then_returns_success(self) -> None:
        session = SessionPalsu([ResponsePalsu(503), ResponsePalsu(200)])
        with patch("app.idx_client.asyncio.sleep", new=AsyncMock()) as sleep:
            response = await self.client._get_with_retry(session, self.params, None)  # noqa: SLF001

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.calls, 2)
        sleep.assert_awaited_once_with(1.0)

    async def test_honors_retry_after(self) -> None:
        session = SessionPalsu([ResponsePalsu(429, {"Retry-After": "3"}), ResponsePalsu(200)])
        with patch("app.idx_client.asyncio.sleep", new=AsyncMock()) as sleep:
            await self.client._get_with_retry(session, self.params, None)  # noqa: SLF001

        sleep.assert_awaited_once_with(3.0)

    async def test_does_not_retry_non_transient_403(self) -> None:
        session = SessionPalsu([ResponsePalsu(403)])
        with patch("app.idx_client.asyncio.sleep", new=AsyncMock()) as sleep:
            response = await self.client._get_with_retry(session, self.params, None)  # noqa: SLF001

        self.assertEqual(response.status_code, 403)
        self.assertEqual(session.calls, 1)
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
