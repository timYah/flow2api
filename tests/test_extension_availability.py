import unittest
from unittest.mock import AsyncMock, patch

from src.core.config import config
from src.core.monitoring import build_public_health_snapshot
from src.core.models import Token
from src.services.browser_captcha_extension import ExtensionCaptchaService
from src.services.load_balancer import LoadBalancer


class _TokenManager:
    def __init__(self, tokens):
        self.tokens = tokens
        self.db = None

    async def get_active_tokens(self):
        return self.tokens


def _token(token_id=1, route_key=None):
    return Token(
        id=token_id,
        st=f"st-{token_id}",
        at=f"at-{token_id}",
        email=f"token-{token_id}@example.com",
        extension_route_key=route_key,
    )


class ExtensionAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_extension_connection_has_actionable_reason(self):
        token = _token()
        balancer = LoadBalancer(_TokenManager([token]))
        original_method = config._config.get("captcha", {}).get("captcha_method")
        config._config.setdefault("captcha", {})["captcha_method"] = "extension"
        try:
            balancer._check_extension_route = AsyncMock(
                return_value=(False, "扩展路由未配置或匿名插件未连接")
            )
            service = type("Service", (), {
                "active_connections": [],
                "describe_routes": lambda self: "",
            })()
            with patch.object(
                ExtensionCaptchaService,
                "get_instance",
                new=AsyncMock(return_value=service),
            ):
                reason = await balancer.get_unavailable_reason(
                    for_image_generation=True,
                    model="gemini-3.1-flash-image-landscape",
                )
            self.assertIn("Chrome 扩展", reason)
            self.assertIn("没有已连接", reason)
        finally:
            if original_method is None:
                config._config.get("captcha", {}).pop("captcha_method", None)
            else:
                config._config.setdefault("captcha", {})["captcha_method"] = original_method

    async def test_health_reports_extension_connection_count(self):
        class DB:
            async def get_all_tokens_with_stats(self):
                return [{"is_active": 1, "at": "at"}]

        service = type("Service", (), {
            "active_connections": [object(), object()],
        })()
        original_method = config._config.get("captcha", {}).get("captcha_method")
        config._config.setdefault("captcha", {})["captcha_method"] = "extension"
        try:
            with patch.object(
                ExtensionCaptchaService,
                "get_instance",
                new=AsyncMock(return_value=service),
            ):
                health = await build_public_health_snapshot(DB())
            self.assertTrue(health["extension_connected"])
            self.assertEqual(health["extension_connection_count"], 2)
        finally:
            if original_method is None:
                config._config.get("captcha", {}).pop("captcha_method", None)
            else:
                config._config.setdefault("captcha", {})["captcha_method"] = original_method

    async def test_route_mismatch_has_actionable_reason(self):
        token = _token(route_key="account-a")
        balancer = LoadBalancer(_TokenManager([token]))
        original_method = config._config.get("captcha", {}).get("captcha_method")
        config._config.setdefault("captcha", {})["captcha_method"] = "extension"
        try:
            balancer._check_extension_route = AsyncMock(
                return_value=(False, "扩展路由 account-a 未连接")
            )
            service = type("Service", (), {
                "active_connections": [object()],
                "describe_routes": lambda self: "account-b",
            })()
            with patch.object(
                ExtensionCaptchaService,
                "get_instance",
                new=AsyncMock(return_value=service),
            ):
                reason = await balancer.get_unavailable_reason(
                    for_image_generation=True,
                    model="gemini-3.1-flash-image-landscape",
                )
            self.assertIn("路由", reason)
            self.assertIn("account-a", reason)
            self.assertIn("account-b", reason)
        finally:
            if original_method is None:
                config._config.get("captcha", {}).pop("captcha_method", None)
            else:
                config._config.setdefault("captcha", {})["captcha_method"] = original_method


if __name__ == "__main__":
    unittest.main()
