import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.sat import service


class TestSatProxySession(unittest.IsolatedAsyncioTestCase):
    async def test_get_and_date_posts_reuse_proxy_session_and_cookie(self):
        session = service._SatProxySession()
        client = AsyncMock(spec=httpx.AsyncClient)
        _dummy_req = httpx.Request("GET", "https://satweb.sat.gov.in/orders")
        get_response = httpx.Response(
            200,
            request=_dummy_req,
            text='<input id="security_token" value="initial-token">',
            headers={"set-cookie": "PHPSESSID=opaque-session-value; Path=/; HttpOnly"},
        )
        first_post_response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://satweb.sat.gov.in/get-orders-by-date"),
            json={"content": "", "token": "rotated-token-1"},
        )
        second_post_response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://satweb.sat.gov.in/get-orders-by-date"),
            json={"content": "", "token": "rotated-token-2"},
        )

        with patch(
            "app.services.sat.service.fetch_with_proxy",
            new=AsyncMock(
                side_effect=[
                    get_response,
                    first_post_response,
                    second_post_response,
                ]
            ),
        ) as fetch:
            token = await service._fetch_security_token(
                client, True, service.SAT_HEADERS, session
            )
            _, token = await service._fetch_date_chunk(
                client,
                "1",
                "01-01-2026",
                "31-03-2026",
                token,
                True,
                service.SAT_HEADERS,
                session,
            )
            _, token = await service._fetch_date_chunk(
                client,
                "1",
                "01-04-2026",
                "30-06-2026",
                token,
                True,
                service.SAT_HEADERS,
                session,
            )

        self.assertEqual(token, "rotated-token-2")
        self.assertEqual(fetch.await_count, 3)

        calls = fetch.await_args_list
        self.assertEqual(
            [call.kwargs["scrapeops_options"] for call in calls],
            [session.scrapeops_options()] * 3,
        )
        self.assertNotIn("Cookie", calls[0].kwargs["headers"])
        self.assertEqual(
            calls[1].kwargs["headers"]["Cookie"],
            "PHPSESSID=opaque-session-value",
        )
        self.assertEqual(
            calls[2].kwargs["headers"]["Cookie"],
            "PHPSESSID=opaque-session-value",
        )
        self.assertEqual(
            calls[1].kwargs["data"]["security_token"],
            "initial-token",
        )
        self.assertEqual(
            calls[2].kwargs["data"]["security_token"],
            "rotated-token-1",
        )

    async def test_proxy_get_warns_when_php_session_cookie_missing(self):
        """PHPSESSID missing in proxy response is a warning, not a fatal error."""
        session = service._SatProxySession()
        client = AsyncMock(spec=httpx.AsyncClient)
        response = httpx.Response(
            200,
            request=httpx.Request("GET", "https://satweb.sat.gov.in/orders"),
            text='<input id="security_token" value="initial-token">',
            # No Set-Cookie header — simulates ScrapeOps not forwarding it.
        )

        with patch(
            "app.services.sat.service.fetch_with_proxy",
            new=AsyncMock(return_value=response),
        ):
            import logging
            with self.assertLogs("app.services.sat.service", level=logging.WARNING) as log_ctx:
                token = await service._fetch_security_token(
                    client, True, service.SAT_HEADERS, session
                )

        self.assertEqual(token, "initial-token")
        self.assertTrue(
            any("PHPSESSID" in line for line in log_ctx.output),
            "Expected a PHPSESSID warning log",
        )
        self.assertNotIn("PHPSESSID", session.cookies)

    async def test_capture_cookies_from_sops_response_cookies(self):
        """Verify sops-response-cookies header returned by ScrapeOps is parsed into session cookies."""
        session = service._SatProxySession()
        client = AsyncMock(spec=httpx.AsyncClient)
        response = httpx.Response(
            200,
            request=httpx.Request("GET", "https://satweb.sat.gov.in/orders"),
            text='<input id="security_token" value="initial-token">',
            headers={
                "sops-response-cookies": "PHPSESSID=scrapeops-session-val; SERVERID_153_web=satweb_app1"
            },
        )

        with patch(
            "app.services.sat.service.fetch_with_proxy",
            new=AsyncMock(return_value=response),
        ):
            token = await service._fetch_security_token(
                client, True, service.SAT_HEADERS, session
            )

        self.assertEqual(token, "initial-token")
        self.assertEqual(session.cookies.get("PHPSESSID"), "scrapeops-session-val")
        self.assertEqual(session.cookies.get("SERVERID_153_web"), "satweb_app1")
        req_headers = session.request_headers(service.SAT_HEADERS)
        self.assertIn("PHPSESSID=scrapeops-session-val", req_headers["Cookie"])
        self.assertIn("SERVERID_153_web=satweb_app1", req_headers["Cookie"])


if __name__ == "__main__":
    unittest.main()
