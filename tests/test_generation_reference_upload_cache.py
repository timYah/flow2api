import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from src.services.generation_handler import GenerationHandler


IMAGE_A = b"image-a"
IMAGE_B = b"image-b"
IMAGE_C = b"image-c"


class GenerationReferenceUploadCacheTests(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, upload_image):
        flow_client = type("FlowClientStub", (), {})()
        flow_client.upload_image = AsyncMock(side_effect=upload_image)
        handler = GenerationHandler(
            flow_client=flow_client,
            token_manager=None,
            load_balancer=None,
            db=None,
            concurrency_manager=None,
            proxy_manager=None,
        )
        return handler, flow_client.upload_image

    async def _get_or_upload(
        self,
        handler,
        image_bytes=IMAGE_A,
        token_id=1,
        project_id="project-1",
        aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
    ):
        return await handler._get_or_upload_reference_image(
            token_id=token_id,
            at=f"at-{token_id}",
            image_bytes=image_bytes,
            aspect_ratio=aspect_ratio,
            project_id=project_id,
        )

    async def test_reuses_successful_upload_for_same_token_project_and_image(self):
        async def fake_upload(*args, **kwargs):
            return "media-1"

        handler, upload_image = self._make_handler(fake_upload)

        first = await self._get_or_upload(handler)
        second = await self._get_or_upload(
            handler,
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        )

        self.assertEqual(first, "media-1")
        self.assertEqual(second, "media-1")
        self.assertEqual(upload_image.await_count, 1)

    async def test_concurrent_requests_share_one_upload(self):
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()

        async def fake_upload(*args, **kwargs):
            upload_started.set()
            await release_upload.wait()
            return "shared-media"

        handler, upload_image = self._make_handler(fake_upload)
        first = asyncio.create_task(self._get_or_upload(handler))
        await upload_started.wait()
        second = asyncio.create_task(self._get_or_upload(handler))
        await asyncio.sleep(0)

        self.assertEqual(upload_image.await_count, 1)
        release_upload.set()
        self.assertEqual(
            await asyncio.gather(first, second),
            ["shared-media", "shared-media"],
        )

    async def test_cancelled_waiter_does_not_cancel_shared_upload(self):
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()

        async def fake_upload(*args, **kwargs):
            upload_started.set()
            await release_upload.wait()
            return "shared-media"

        handler, upload_image = self._make_handler(fake_upload)
        cancelled_waiter = asyncio.create_task(self._get_or_upload(handler))
        await upload_started.wait()
        active_waiter = asyncio.create_task(self._get_or_upload(handler))
        await asyncio.sleep(0)

        cancelled_waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled_waiter

        release_upload.set()
        self.assertEqual(await active_waiter, "shared-media")
        self.assertEqual(await self._get_or_upload(handler), "shared-media")
        self.assertEqual(upload_image.await_count, 1)

    async def test_cache_is_isolated_by_token_project_and_image_content(self):
        upload_count = 0

        async def fake_upload(*args, **kwargs):
            nonlocal upload_count
            upload_count += 1
            return f"media-{upload_count}"

        handler, upload_image = self._make_handler(fake_upload)

        await self._get_or_upload(handler, token_id=1, project_id="project-1", image_bytes=IMAGE_A)
        await self._get_or_upload(handler, token_id=2, project_id="project-1", image_bytes=IMAGE_A)
        await self._get_or_upload(handler, token_id=1, project_id="project-2", image_bytes=IMAGE_A)
        await self._get_or_upload(handler, token_id=1, project_id="project-1", image_bytes=IMAGE_B)

        self.assertEqual(upload_image.await_count, 4)

    async def test_failed_shared_upload_is_not_cached_and_can_retry(self):
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()
        upload_count = 0

        async def fake_upload(*args, **kwargs):
            nonlocal upload_count
            upload_count += 1
            if upload_count == 1:
                upload_started.set()
                await release_upload.wait()
                raise RuntimeError("upload failed")
            return "media-after-retry"

        handler, upload_image = self._make_handler(fake_upload)
        first = asyncio.create_task(self._get_or_upload(handler))
        await upload_started.wait()
        second = asyncio.create_task(self._get_or_upload(handler))
        await asyncio.sleep(0)
        release_upload.set()

        failures = await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(upload_image.await_count, 1)
        self.assertTrue(all(isinstance(error, RuntimeError) for error in failures))

        self.assertEqual(await self._get_or_upload(handler), "media-after-retry")
        self.assertEqual(upload_image.await_count, 2)

    async def test_expired_entry_is_uploaded_again(self):
        upload_count = 0

        async def fake_upload(*args, **kwargs):
            nonlocal upload_count
            upload_count += 1
            return f"media-{upload_count}"

        handler, upload_image = self._make_handler(fake_upload)
        handler.REFERENCE_UPLOAD_CACHE_TTL_SECONDS = 1

        self.assertEqual(await self._get_or_upload(handler), "media-1")
        cache_key = next(iter(handler._reference_upload_cache))
        media_id, _ = handler._reference_upload_cache[cache_key]
        handler._reference_upload_cache[cache_key] = (media_id, time.monotonic() - 2)

        self.assertEqual(await self._get_or_upload(handler), "media-2")
        self.assertEqual(upload_image.await_count, 2)

    async def test_lru_limit_evicts_least_recently_used_entry(self):
        upload_count = 0

        async def fake_upload(*args, **kwargs):
            nonlocal upload_count
            upload_count += 1
            return f"media-{upload_count}"

        handler, upload_image = self._make_handler(fake_upload)
        handler.REFERENCE_UPLOAD_CACHE_MAX_ENTRIES = 2

        await self._get_or_upload(handler, image_bytes=IMAGE_A)
        await self._get_or_upload(handler, image_bytes=IMAGE_B)
        await self._get_or_upload(handler, image_bytes=IMAGE_A)
        await self._get_or_upload(handler, image_bytes=IMAGE_C)
        await self._get_or_upload(handler, image_bytes=IMAGE_B)

        self.assertEqual(upload_image.await_count, 4)
        self.assertEqual(len(handler._reference_upload_cache), 2)


if __name__ == "__main__":
    unittest.main()
