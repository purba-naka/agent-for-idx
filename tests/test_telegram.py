from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from app.telegram import _post_with_retry


class ResponsePalsu:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.headers: dict[str, str] = {}

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._body


class ClientPalsu:
    def __init__(self, responses: list[ResponsePalsu]) -> None:
        self.responses = responses
        self.calls = 0

    async def post(self, *_args: Any, **_kwargs: Any) -> ResponsePalsu:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class TelegramRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_honors_telegram_retry_after(self) -> None:
        client = ClientPalsu(
            [
                ResponsePalsu(429, {"ok": False, "parameters": {"retry_after": 4}}),
                ResponsePalsu(200, {"ok": True}),
            ]
        )
        with patch("app.telegram.asyncio.sleep", new=AsyncMock()) as sleep:
            await _post_with_retry(client, "https://telegram.example", {"text": "uji"})

        self.assertEqual(client.calls, 2)
        sleep.assert_awaited_once_with(4.0)

    async def test_retries_server_error(self) -> None:
        client = ClientPalsu(
            [ResponsePalsu(503, {"ok": False}), ResponsePalsu(200, {"ok": True})]
        )
        with patch("app.telegram.asyncio.sleep", new=AsyncMock()) as sleep:
            await _post_with_retry(client, "https://telegram.example", {"text": "uji"})

        self.assertEqual(client.calls, 2)
        sleep.assert_awaited_once_with(1.0)

    async def test_does_not_retry_permanent_error(self) -> None:
        client = ClientPalsu([ResponsePalsu(400, {"ok": False, "description": "bad request"})])
        with (
            patch("app.telegram.asyncio.sleep", new=AsyncMock()) as sleep,
            self.assertRaisesRegex(RuntimeError, r"Telegram API gagal \(400\)"),
        ):
            await _post_with_retry(client, "https://telegram.example", {"text": "uji"})

        self.assertEqual(client.calls, 1)
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
