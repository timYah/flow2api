import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.models import Token
from src.services.token_manager import TokenManager


class UnusualActivityTokenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = AsyncMock()
        self.flow_client = MagicMock()
        self.token_manager = TokenManager(self.db, self.flow_client)

    async def test_ban_token_for_unusual_activity(self):
        await self.token_manager.ban_token_for_unusual_activity(1)
        self.db.update_token.assert_awaited_once()
        args, kwargs = self.db.update_token.await_args
        self.assertEqual(args[0], 1)
        self.assertFalse(kwargs["is_active"])
        self.assertEqual(kwargs["ban_reason"], "unusual_activity")
        self.assertIsInstance(kwargs["banned_at"], datetime)

    async def test_auto_unban_unusual_activity_token_after_one_hour(self):
        now = datetime.now(timezone.utc)
        two_hours_ago = now - timedelta(hours=2)
        valid_expires = now + timedelta(days=5)

        token = Token(
            id=1,
            st="test-st",
            at="test-at",
            email="test@example.com",
            is_active=False,
            ban_reason="unusual_activity",
            banned_at=two_hours_ago,
            at_expires=valid_expires,
        )
        self.db.get_all_tokens.return_value = [token]

        await self.token_manager.auto_unban_429_tokens()
        self.db.update_token.assert_awaited_once_with(
            1,
            is_active=True,
            ban_reason=None,
            banned_at=None,
        )

    async def test_auto_unban_does_not_unban_unusual_activity_too_early(self):
        now = datetime.now(timezone.utc)
        ten_mins_ago = now - timedelta(minutes=10)
        valid_expires = now + timedelta(days=5)

        token = Token(
            id=1,
            st="test-st",
            at="test-at",
            email="test@example.com",
            is_active=False,
            ban_reason="unusual_activity",
            banned_at=ten_mins_ago,
            at_expires=valid_expires,
        )
        self.db.get_all_tokens.return_value = [token]

        await self.token_manager.auto_unban_429_tokens()
        self.db.update_token.assert_not_awaited()

    async def test_handle_generation_failover_on_unusual_activity(self):
        from src.services.generation_handler import GenerationHandler
        from src.core.models import Token

        token1 = Token(id=1, email="t1@example.com", is_active=True, user_paygate_tier="PAYGATE_TIER_ONE", st="st1")
        token2 = Token(id=2, email="t2@example.com", is_active=True, user_paygate_tier="PAYGATE_TIER_ONE", st="st2")

        handler = GenerationHandler.__new__(GenerationHandler)
        handler.token_manager = MagicMock()
        handler.token_manager.ban_token_for_unusual_activity = AsyncMock()
        handler.token_manager.ensure_valid_token = AsyncMock(side_effect=lambda t: t)
        handler.token_manager.ensure_project_exists = AsyncMock(return_value="proj-123")
        handler.token_manager.record_usage = AsyncMock()
        handler.token_manager.record_success = AsyncMock()
        handler.load_balancer = MagicMock()
        handler.load_balancer.select_token = AsyncMock(side_effect=[token1, token2])
        handler.load_balancer.release_pending = AsyncMock()
        handler.flow_client = MagicMock()
        handler.flow_client.prefill_remote_browser_pool = AsyncMock()

        async def fake_image_gen(token, *args, **kwargs):
            generation_result = kwargs.get("generation_result", {})
            if token.id == 1:
                raise RuntimeError("PUBLIC_ERROR_UNUSUAL_ACTIVITY: reCAPTCHA evaluation failed")
            generation_result["success"] = True
            yield "data: ok"

        handler._handle_image_generation = MagicMock(side_effect=fake_image_gen)
        handler._create_generation_result = lambda: {"success": False, "error_message": None}
        handler._update_request_log_progress = AsyncMock()
        handler._log_request = AsyncMock()
        handler._should_count_token_error = MagicMock(return_value=False)
        handler._create_stream_chunk = lambda msg, **kw: msg

        chunks = []
        async for chunk in handler.handle_generation(
            model="gemini-3.1-flash-image-square",
            prompt="test prompt",
            stream=True
        ):
            chunks.append(chunk)

        handler.token_manager.ban_token_for_unusual_activity.assert_awaited_once_with(1)
        self.assertEqual(handler.load_balancer.select_token.await_count, 2)
        self.assertIn("data: ok", chunks)


if __name__ == "__main__":
    unittest.main()
