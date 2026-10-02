import unittest
from unittest.mock import AsyncMock, patch

from src.services.flow_client import FlowClient


JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 16


class FlowClientUploadImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_scoped_upload_uses_new_endpoint_with_project_id(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            return {
                "media": {
                    "name": "new-media-id",
                }
            }

        client._make_request = AsyncMock(side_effect=fake_make_request)

        media_id = await client.upload_image(
            at="test-at",
            image_bytes=JPEG_BYTES,
            aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
            project_id="project-123",
        )

        self.assertEqual(media_id, "new-media-id")
        self.assertEqual(len(request_calls), 1)
        self.assertTrue(request_calls[0]["url"].endswith("/flow/uploadImage"))
        self.assertEqual(
            request_calls[0]["json_data"]["clientContext"]["projectId"],
            "project-123",
        )
        self.assertIn("sessionId", request_calls[0]["json_data"]["clientContext"])

    async def test_project_scoped_upload_accepts_media_list_response(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            return {
                "media": [
                    {
                        "name": "new-media-id",
                        "projectId": "project-123",
                    }
                ]
            }

        client._make_request = AsyncMock(side_effect=fake_make_request)

        media_id = await client.upload_image(
            at="test-at",
            image_bytes=JPEG_BYTES,
            aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
            project_id="project-123",
        )

        self.assertEqual(media_id, "new-media-id")
        self.assertEqual(len(request_calls), 1)
        self.assertTrue(request_calls[0]["url"].endswith("/flow/uploadImage"))

    async def test_project_scoped_upload_does_not_fallback_to_legacy_endpoint(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            if kwargs["url"].endswith("/flow/uploadImage"):
                raise RuntimeError("HTTP 500: upstream failed")
            self.fail("带 project_id 的上传不应回退到 legacy 接口")

        client._make_request = AsyncMock(side_effect=fake_make_request)

        with patch("src.services.flow_client.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "legacy :uploadUserImage fallback is disabled"):
                await client.upload_image(
                    at="test-at",
                    image_bytes=JPEG_BYTES,
                    aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                    project_id="project-123",
                )

        self.assertEqual(len(request_calls), 3)
        for call in request_calls:
            self.assertTrue(call["url"].endswith("/flow/uploadImage"))
            self.assertEqual(
                call["json_data"]["clientContext"]["projectId"],
                "project-123",
            )

    async def test_project_scoped_upload_retries_on_transient_error_and_succeeds(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            if len(request_calls) == 1:
                raise RuntimeError("HTTP Error 503: Service Unavailable")
            return {
                "media": {
                    "name": "retry-succeeded-media-id",
                }
            }

        client._make_request = AsyncMock(side_effect=fake_make_request)

        with patch("src.services.flow_client.asyncio.sleep", new=AsyncMock()):
            media_id = await client.upload_image(
                at="test-at",
                image_bytes=JPEG_BYTES,
                aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                project_id="project-123",
            )

        self.assertEqual(media_id, "retry-succeeded-media-id")
        self.assertEqual(len(request_calls), 2)
        self.assertTrue(all(call["url"].endswith("/flow/uploadImage") for call in request_calls))

    async def test_project_scoped_upload_fails_fast_on_bad_request(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            raise RuntimeError("HTTP Error 400: Bad Request")

        client._make_request = AsyncMock(side_effect=fake_make_request)

        with patch("src.services.flow_client.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "legacy :uploadUserImage fallback is disabled"):
                await client.upload_image(
                    at="test-at",
                    image_bytes=JPEG_BYTES,
                    aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                    project_id="project-123",
                )

        self.assertEqual(len(request_calls), 1)
        self.assertEqual(
            request_calls[0]["json_data"]["clientContext"]["projectId"],
            "project-123",
        )

    async def test_project_scoped_upload_includes_underlying_error_in_exception_message(self):
        client = FlowClient(proxy_manager=None)

        async def fake_make_request(**kwargs):
            raise RuntimeError("HTTP Error 503: upstream service overloaded")

        client._make_request = AsyncMock(side_effect=fake_make_request)

        with patch("src.services.flow_client.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError) as ctx:
                await client.upload_image(
                    at="test-at",
                    image_bytes=JPEG_BYTES,
                    aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                    project_id="project-123",
                )

        err_msg = str(ctx.exception)
        self.assertIn("HTTP Error 503: upstream service overloaded", err_msg)
        self.assertIn("legacy :uploadUserImage fallback is disabled", err_msg)
        self.assertIn("project_id=project-123", err_msg)

    async def test_project_scoped_upload_passes_browser_headers_and_referer(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            return {"media": {"name": "media-1"}}

        client._make_request = AsyncMock(side_effect=fake_make_request)

        await client.upload_image(
            at="test-at",
            image_bytes=JPEG_BYTES,
            aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
            project_id="project-xyz",
        )

        self.assertEqual(len(request_calls), 1)
        headers = request_calls[0].get("headers", {})
        self.assertEqual(headers.get("x-browser-channel"), "stable")
        self.assertIn("project-xyz", headers.get("Referer", ""))

    def test_is_retryable_upload_error(self):
        client = FlowClient(proxy_manager=None)

        retryable_errors = [
            RuntimeError("HTTP Error 500: Internal Server Error"),
            RuntimeError("HTTP Error 502: Bad Gateway"),
            RuntimeError("HTTP Error 503: Service Unavailable"),
            RuntimeError("HTTP Error 504: Gateway Timeout"),
            RuntimeError("UNAVAILABLE: The service is currently unavailable"),
            RuntimeError("RESOURCE_EXHAUSTED: Rate limit exceeded"),
            RuntimeError("DEADLINE_EXCEEDED"),
            RuntimeError("HTTP Error 429: Too Many Requests"),
            RuntimeError("Connection timed out"),
            RuntimeError("curl: (28) Operation timed out"),
            RuntimeError("Invalid upload response: missing media id"),
            RuntimeError("RemoteDisconnected: connection closed by peer"),
        ]
        for err in retryable_errors:
            is_retryable, reason = client._is_retryable_upload_error(err)
            self.assertTrue(is_retryable, f"Expected {err} to be retryable")
            self.assertTrue(bool(reason))

        non_retryable_errors = [
            RuntimeError("HTTP Error 400: Bad Request"),
            RuntimeError("HTTP Error 404: Not Found"),
            RuntimeError("INVALID_ARGUMENT: image dimensions invalid"),
            RuntimeError("HTTP Error 415: Unsupported Media Type"),
        ]
        for err in non_retryable_errors:
            is_retryable, reason = client._is_retryable_upload_error(err)
            self.assertFalse(is_retryable, f"Expected {err} to not be retryable")

    async def test_upload_without_project_id_keeps_legacy_fallback(self):
        client = FlowClient(proxy_manager=None)

        request_calls = []

        async def fake_make_request(**kwargs):
            request_calls.append(kwargs)
            if kwargs["url"].endswith("/flow/uploadImage"):
                raise RuntimeError("HTTP 500: upstream failed")
            if kwargs["url"].endswith(":uploadUserImage"):
                return {
                    "mediaGenerationId": {
                        "mediaGenerationId": "legacy-media-id",
                    }
                }
            self.fail(f"Unexpected url: {kwargs['url']}")

        client._make_request = AsyncMock(side_effect=fake_make_request)

        media_id = await client.upload_image(
            at="test-at",
            image_bytes=JPEG_BYTES,
            aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
            project_id=None,
        )

        self.assertEqual(media_id, "legacy-media-id")
        self.assertEqual(len(request_calls), 2)
        self.assertNotIn(
            "projectId",
            request_calls[1]["json_data"]["clientContext"],
        )


if __name__ == "__main__":
    unittest.main()
