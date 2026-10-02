import asyncio
import os
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from src.services.browser_captcha import (
    BrowserCaptchaService,
    TokenBrowser,
    delete_account_browser_profile,
    get_account_browser_profile_dir,
)


class _FakePersistentContext:
    def __init__(self):
        self.browser = None
        self.close = AsyncMock()
        self.add_init_script = AsyncMock()

    async def new_page(self):
        raise AssertionError("keepalive page is not part of this unit test")


class _FakeChromium:
    def __init__(self, context):
        self.context = context
        self.launch_persistent_context = AsyncMock(return_value=context)
        self.launch = AsyncMock()


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stop = AsyncMock()


class _FakePlaywrightFactory:
    def __init__(self, playwright):
        self.playwright = playwright
        self.start = AsyncMock(return_value=playwright)


class _FakeAccountBrowser:
    def __init__(self, token_id, idle_since, busy=False, live=True):
        self.token_id = token_id
        self.user_data_dir = f"profile-{token_id}"
        self._idle_since = idle_since
        self._busy = busy
        self._live = live
        self.recycle_browser = AsyncMock(side_effect=self._recycle)

    def has_shared_browser(self):
        return self._live

    def is_busy(self):
        return self._busy

    def idle_seconds(self):
        return self._idle_since

    async def _recycle(self, *args, **kwargs):
        self._live = False


class BrowserCaptchaProfileTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_path_is_account_scoped_and_cleanup_is_exact(self):
        with tempfile.TemporaryDirectory() as base_dir:
            first = get_account_browser_profile_dir(7, base_dir)
            sibling = get_account_browser_profile_dir(8, base_dir)
            os.makedirs(first)
            os.makedirs(sibling)
            with open(os.path.join(first, "Cookies"), "w", encoding="utf-8") as handle:
                handle.write("first")
            with open(os.path.join(sibling, "Cookies"), "w", encoding="utf-8") as handle:
                handle.write("sibling")

            self.assertEqual(
                first,
                os.path.join(os.path.abspath(base_dir), "accounts", "token_7"),
            )
            self.assertTrue(delete_account_browser_profile(7, base_dir))
            self.assertFalse(os.path.exists(first))
            self.assertTrue(os.path.exists(sibling))

    async def test_account_browser_uses_persistent_context_and_exact_profile(self):
        with tempfile.TemporaryDirectory() as base_dir:
            context = _FakePersistentContext()
            chromium = _FakeChromium(context)
            playwright = _FakePlaywright(chromium)
            factory = _FakePlaywrightFactory(playwright)
            browser = TokenBrowser(12, get_account_browser_profile_dir(12, base_dir), db=None)
            browser._cleanup_stale_account_process = AsyncMock()
            browser._extract_browser_pid = lambda _browser: None

            with patch("src.services.browser_captcha.async_playwright", return_value=factory):
                _playwright, _browser, returned_context = await browser._create_browser()

            factory.start.assert_awaited_once()
            chromium.launch_persistent_context.assert_awaited_once()
            launch_kwargs = chromium.launch_persistent_context.await_args.kwargs
            self.assertEqual(
                launch_kwargs["user_data_dir"],
                get_account_browser_profile_dir(12, base_dir),
            )
            chromium.launch.assert_not_awaited()
            self.assertIs(returned_context, context)
            self.assertTrue(context._flow2api_persistent_context)

    async def test_service_maps_each_token_to_its_own_profile(self):
        with tempfile.TemporaryDirectory() as base_dir:
            service = BrowserCaptchaService()
            service.base_user_data_dir = base_dir

            first = await service._get_or_create_browser(101)
            second = await service._get_or_create_browser(202)

            self.assertEqual(
                first.user_data_dir,
                get_account_browser_profile_dir(101, base_dir),
            )
            self.assertEqual(
                second.user_data_dir,
                get_account_browser_profile_dir(202, base_dir),
            )
            self.assertIsNot(first, second)
            self.assertNotEqual(first.user_data_dir, second.user_data_dir)
            self.assertFalse(os.path.exists(os.path.join(base_dir, "browser_0")))

    async def test_capacity_evicts_oldest_idle_account_without_deleting_profile(self):
        with tempfile.TemporaryDirectory() as base_dir:
            service = BrowserCaptchaService()
            service.base_user_data_dir = base_dir
            service._browser_count = 1
            old_browser = _FakeAccountBrowser(1, idle_since=1)
            service._browsers[1] = old_browser

            target = await service._acquire_account_browser(2)

            self.assertEqual(target.token_id, 2)
            old_browser.recycle_browser.assert_awaited_once()
            self.assertTrue(os.path.exists(base_dir))
            self.assertEqual(service._account_reservations.get(2), 1)
            await service._release_account_reservation(2)

    async def test_busy_account_context_makes_new_account_wait(self):
        service = BrowserCaptchaService()
        service._browser_count = 1
        busy_browser = _FakeAccountBrowser(1, idle_since=1, busy=True)
        service._browsers[1] = busy_browser

        acquire_task = asyncio.create_task(service._acquire_account_browser(2))
        await asyncio.sleep(0)
        self.assertFalse(acquire_task.done())

        busy_browser._busy = False
        async with service._capacity_condition:
            service._capacity_condition.notify_all()
        target = await asyncio.wait_for(acquire_task, timeout=1)
        self.assertEqual(target.token_id, 2)
        await service._release_account_reservation(2)

    async def test_remove_token_profile_keeps_sibling_profile(self):
        with tempfile.TemporaryDirectory() as base_dir:
            service = BrowserCaptchaService()
            service.base_user_data_dir = base_dir
            first = get_account_browser_profile_dir(21, base_dir)
            sibling = get_account_browser_profile_dir(22, base_dir)
            os.makedirs(first)
            os.makedirs(sibling)

            browser = types.SimpleNamespace(
                token_id=21,
                wait_until_idle=AsyncMock(return_value=True),
                force_close_pending_browser=AsyncMock(),
                has_shared_browser=lambda: False,
            )
            service._browsers[21] = browser

            self.assertTrue(await service.remove_token(21))
            browser.wait_until_idle.assert_awaited_once()
            browser.force_close_pending_browser.assert_awaited_once_with(close_all=True)
            self.assertFalse(os.path.exists(first))
            self.assertTrue(os.path.isdir(sibling))

    async def test_account_operation_is_not_blocked_by_diagnostic_limiter(self):
        service = BrowserCaptchaService()
        service._check_available = lambda: None
        await service._diagnostic_semaphore.acquire()
        browser = types.SimpleNamespace(
            get_token=AsyncMock(return_value=("captcha-token", None)),
        )
        service._resolve_token_proxy_url = AsyncMock(return_value=None)
        service._acquire_account_browser = AsyncMock(return_value=browser)

        try:
            result = await asyncio.wait_for(
                service.get_token("project-1", token_id=31),
                timeout=1,
            )
        finally:
            service._diagnostic_semaphore.release()

        self.assertEqual(result, ("captcha-token", 31))
        browser.get_token.assert_awaited_once()

    async def test_remove_token_cancellation_clears_removal_marker(self):
        service = BrowserCaptchaService()
        service._account_reservations[41] = 1

        removal_task = asyncio.create_task(service.remove_token(41))
        await asyncio.sleep(0.01)
        self.assertIn(41, service._removing_token_ids)

        removal_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await removal_task
        self.assertNotIn(41, service._removing_token_ids)


if __name__ == "__main__":
    unittest.main()
