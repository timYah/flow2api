import asyncio
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.models import Token, TokenRefreshConfig
from src.services.token_manager import TokenManager


class AutoRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_at_refresh_once_refreshes_expiring_tokens(self):
        db = AsyncMock()
        flow = AsyncMock()
        token_mgr = TokenManager(db=db, flow_client=flow)

        db.get_token_refresh_config.return_value = TokenRefreshConfig(enabled=True)

        now = datetime.now(timezone.utc)
        # Token 1: expires in 30 minutes (< 1h), needs refresh
        token1 = Token(
            id=1,
            email="expiring@test.com",
            st="st-1",
            at="at-1",
            at_expires=now + timedelta(minutes=30),
            is_active=True,
            auto_refresh_enabled=True,
            protocol_mode="session",
        )
        # Token 2: expires in 2 hours (> 1h), does NOT need refresh
        token2 = Token(
            id=2,
            email="fresh@test.com",
            st="st-2",
            at="at-2",
            at_expires=now + timedelta(hours=2),
            is_active=True,
            auto_refresh_enabled=True,
            protocol_mode="session",
        )
        # Token 3: auto_refresh_enabled = False, skipped
        token3 = Token(
            id=3,
            email="disabled@test.com",
            st="st-3",
            at="at-3",
            at_expires=now + timedelta(minutes=10),
            is_active=True,
            auto_refresh_enabled=False,
            protocol_mode="session",
        )

        db.get_active_tokens.return_value = [token1, token2, token3]
        db.get_token.return_value = token1

        token_mgr._refresh_at = AsyncMock(return_value=True)

        await token_mgr.run_at_refresh_once()

        # Only token 1 should have been refreshed
        token_mgr._refresh_at.assert_awaited_once_with(1)

    async def test_run_at_refresh_once_skips_when_global_config_disabled(self):
        db = AsyncMock()
        flow = AsyncMock()
        token_mgr = TokenManager(db=db, flow_client=flow)

        db.get_token_refresh_config.return_value = TokenRefreshConfig(enabled=False)
        token_mgr._refresh_at = AsyncMock(return_value=True)

        await token_mgr.run_at_refresh_once()

        token_mgr._refresh_at.assert_not_awaited()
        db.get_active_tokens.assert_not_awaited()

    async def test_run_at_refresh_once_respects_throttle_cooldown(self):
        db = AsyncMock()
        flow = AsyncMock()
        token_mgr = TokenManager(db=db, flow_client=flow)

        db.get_token_refresh_config.return_value = TokenRefreshConfig(enabled=True)

        now = datetime.now(timezone.utc)
        # Expires in 20 minutes (> 5 minutes, so subject to 300s cooldown)
        token = Token(
            id=1,
            email="expiring@test.com",
            st="st-1",
            at="at-1",
            at_expires=now + timedelta(minutes=20),
            is_active=True,
            auto_refresh_enabled=True,
            protocol_mode="session",
        )
        db.get_active_tokens.return_value = [token]
        db.get_token.return_value = token
        token_mgr._refresh_at = AsyncMock(return_value=False)

        # First run: attempts refresh
        await token_mgr.run_at_refresh_once()
        self.assertEqual(token_mgr._refresh_at.await_count, 1)

        # Second run immediately after: throttled by cooldown
        await token_mgr.run_at_refresh_once()
        self.assertEqual(token_mgr._refresh_at.await_count, 1)

    async def test_run_at_refresh_once_urgent_token_bypasses_cooldown(self):
        db = AsyncMock()
        flow = AsyncMock()
        token_mgr = TokenManager(db=db, flow_client=flow)

        db.get_token_refresh_config.return_value = TokenRefreshConfig(enabled=True)

        now = datetime.now(timezone.utc)
        # Expires in 2 minutes (<= 5 minutes, urgent!)
        token = Token(
            id=1,
            email="urgent@test.com",
            st="st-1",
            at="at-1",
            at_expires=now + timedelta(minutes=2),
            is_active=True,
            auto_refresh_enabled=True,
            protocol_mode="session",
        )
        db.get_active_tokens.return_value = [token]
        db.get_token.return_value = token
        token_mgr._refresh_at = AsyncMock(return_value=False)

        # First run
        await token_mgr.run_at_refresh_once()
        self.assertEqual(token_mgr._refresh_at.await_count, 1)

        # Second run: urgent token (< 300s) bypasses cooldown and retries
        await token_mgr.run_at_refresh_once()
        self.assertEqual(token_mgr._refresh_at.await_count, 2)

    async def test_run_auto_refresh_once_runs_both_at_and_protocol(self):
        db = AsyncMock()
        flow = AsyncMock()
        token_mgr = TokenManager(db=db, flow_client=flow)

        token_mgr.run_at_refresh_once = AsyncMock()
        token_mgr.run_protocol_refresh_once = AsyncMock()

        await token_mgr.run_auto_refresh_once()

        token_mgr.run_at_refresh_once.assert_awaited_once()
        token_mgr.run_protocol_refresh_once.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
