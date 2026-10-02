import unittest
from unittest.mock import AsyncMock, patch

from src.services.browser_captcha_extension import (
    ExtensionCaptchaService,
    ExtensionInfrastructureError,
)
from src.services.flow_client import FlowClient
from src.services.generation_handler import GenerationHandler


class BrowserCaptchaExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_token_bundle_preserves_extension_runtime_metadata(self):
        service = ExtensionCaptchaService()
        websocket = AsyncMock()
        service.active_connections = []

        class FakeFuture:
            pass

        # Exercise the public service shape through a mocked connection lookup;
        # the websocket response is the same payload sent by the MV3 worker.
        service._select_connection = lambda route_key: type(
            "Connection",
            (),
            {"websocket": websocket, "route_key": "", "client_label": "test"},
        )()
        service._resolve_route_key = AsyncMock(return_value="")

        async def send_text(payload):
            request = __import__("json").loads(payload)
            service.pending_requests[request["req_id"]][0].set_result({
                "status": "success",
                "token": "token-123",
                "fingerprint": {
                    "user_agent": "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36",
                    "accept_language": "en-US,en;q=0.9",
                    "sec_ch_ua": '"Google Chrome";v="140"',
                    "sec_ch_ua_mobile": "?0",
                    "sec_ch_ua_platform": '"macOS"',
                },
                "session_cookies": {"SID": "cookie-value"},
                "page_url": "https://labs.google/fx/tools/flow/project/project-1",
            })

        websocket.send_text.side_effect = send_text
        service.active_connections = [type(
            "Connection",
            (),
            {"websocket": websocket, "route_key": "", "client_label": "test"},
        )()]

        bundle = await service.get_token_bundle("project-1", token_id=1)

        self.assertEqual(bundle["token"], "token-123")
        self.assertEqual(bundle["fingerprint"]["user_agent"], "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36")
        self.assertEqual(bundle["session_cookies"]["SID"], "cookie-value")

    async def test_flow_client_binds_extension_fingerprint_to_request_context(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.get_token_bundle.return_value = {
            "token": "token-123",
            "fingerprint": {
                "user_agent": "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36",
                "accept_language": "en-US,en;q=0.9",
            },
            "session_cookies": {"SID": "cookie-value"},
            "page_url": "https://labs.google/fx/tools/flow/project/project-1",
        }

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ):
            cfg.captcha_method = "extension"
            token, browser_id = await flow._get_recaptcha_token("project-1", token_id=1)

        self.assertEqual(token, "token-123")
        self.assertIsNone(browser_id)
        fingerprint = flow.get_request_fingerprint()
        self.assertEqual(fingerprint["user_agent"], "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36")
        self.assertEqual(fingerprint["session_cookies"]["SID"], "cookie-value")
        self.assertEqual(fingerprint["project_id"], "project-1")
        self.assertEqual(fingerprint["referer"], "https://labs.google/fx/tools/flow/project/project-1")

    async def test_submit_flow_request_dispatches_full_request_to_extension(self):
        service = ExtensionCaptchaService()
        websocket = AsyncMock()
        connection = type(
            "Connection",
            (),
            {"websocket": websocket, "route_key": "route-1", "client_label": "test"},
        )()
        service.active_connections = [connection]
        service._select_connection = lambda route_key: connection
        service._resolve_route_key = AsyncMock(return_value="route-1")

        async def send_text(payload):
            request = __import__("json").loads(payload)
            self.assertEqual(request["type"], "submit_flow_request")
            self.assertEqual(request["flow_request"]["url"], "https://aisandbox-pa.googleapis.com/v1/projects/p/flowMedia:batchGenerateImages")
            self.assertEqual(request["flow_request"]["at_token"], "at-token")
            service.pending_requests[request["req_id"]][0].set_result({
                "status": "success",
                "flow_response": {
                    "ok": True,
                    "status": 200,
                    "text": '{"media": []}',
                },
                "fingerprint": {"user_agent": "Mozilla/5.0 Chrome/140.0.0.0"},
                "session_cookies": {"SID": "cookie-value"},
                "page_url": "https://labs.google/fx/tools/flow/project/p",
            })

        websocket.send_text.side_effect = send_text
        response = await service.submit_flow_request(
            project_id="p",
            action="IMAGE_GENERATION",
            url="https://aisandbox-pa.googleapis.com/v1/projects/p/flowMedia:batchGenerateImages",
            at_token="at-token",
            json_data={"clientContext": {"projectId": "p"}},
            timeout=20,
            token_id=1,
        )

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["fingerprint"]["user_agent"], "Mozilla/5.0 Chrome/140.0.0.0")
        self.assertEqual(response["session_cookies"]["SID"], "cookie-value")

    async def test_submit_flow_request_preserves_structured_extension_failure(self):
        service = ExtensionCaptchaService()
        websocket = AsyncMock()
        connection = type(
            "Connection",
            (),
            {"websocket": websocket, "route_key": "route-1", "client_label": "test"},
        )()
        service.active_connections = [connection]
        service._select_connection = lambda route_key: connection
        service._resolve_route_key = AsyncMock(return_value="route-1")

        async def send_text(payload):
            request = __import__("json").loads(payload)
            service.pending_requests[request["req_id"]][0].set_result({
                "status": "error",
                "error_code": "empty_recaptcha_token",
                "phase": "recaptcha_execute",
                "error": "reCAPTCHA returned an empty token",
                "diagnostics": {"phase": "recaptcha_execute"},
            })

        websocket.send_text.side_effect = send_text

        with self.assertRaises(ExtensionInfrastructureError) as context:
            await service.submit_flow_request(
                project_id="p",
                action="IMAGE_GENERATION",
                url="https://aisandbox-pa.googleapis.com/v1/projects/p/flowMedia:batchGenerateImages",
                at_token="at-token",
                json_data={"clientContext": {"projectId": "p"}},
                timeout=20,
                token_id=1,
            )

        self.assertEqual(context.exception.phase, "recaptcha_execute")
        self.assertIn("empty_recaptcha_token", str(context.exception))
        self.assertFalse(context.exception.count_as_token_error)
        self.assertFalse(service.pending_requests)

    async def test_extension_token_errors_are_not_swallowed_by_flow_client(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.get_token_bundle.side_effect = ExtensionInfrastructureError(
            "empty_recaptcha_token: reCAPTCHA returned an empty token",
            phase="recaptcha_execute",
        )

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ):
            cfg.captcha_method = "extension"
            with self.assertRaises(ExtensionInfrastructureError):
                await flow._get_recaptcha_token("project-1", token_id=1)

    def test_extension_failures_are_excluded_from_token_error_count(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        self.assertFalse(
            handler._should_count_token_error(
                ExtensionInfrastructureError("empty_recaptcha_token", phase="recaptcha_execute")
            )
        )
        self.assertFalse(
            handler._should_count_token_error(
                "生成失败: Extension script failed at recaptcha_execute: empty token"
            )
        )
        self.assertFalse(
            handler._should_count_token_error(
                "生成失败: Timed out waiting for Chrome Extension Flow request"
            )
        )
        self.assertTrue(handler._should_count_token_error("HTTP Error 403: account disabled"))

    def test_flow_fetch_retry_classification_preserves_non_retryable_phases(self):
        flow = FlowClient(proxy_manager=None)

        retryable_errors = [
            "Extension script failed at flow_fetch",
            "Extension script failed at flow_fetch: TypeError: Failed to fetch",
            "Extension script failed at flow_fetch: NetworkError when attempting to fetch resource",
            "Extension script failed at flow_fetch: AbortError: The user aborted a request",
            "phase=flow_fetch: net::ERR_CONNECTION_RESET",
            ExtensionInfrastructureError("request failed", phase="flow_fetch"),
        ]
        for error in retryable_errors:
            with self.subTest(error=str(error)):
                self.assertTrue(flow._is_retryable_extension_flow_fetch_error(error))
                self.assertEqual(flow._get_retry_reason(error), "扩展 Flow fetch 临时错误")

        non_retryable_errors = [
            "Extension script failed at flow_request_validation: invalid_flow_url",
            "Extension script failed at flow_request_validation: disallowed_flow_url",
            "Extension script failed at flow_fetch: HTTP Error 403",
            "Extension script failed at flow_fetch: reCAPTCHA evaluation failed",
            ExtensionInfrastructureError("empty token", phase="recaptcha_execute"),
        ]
        for error in non_retryable_errors:
            with self.subTest(error=str(error)):
                self.assertFalse(flow._is_retryable_extension_flow_fetch_error(error))
                self.assertIsNone(flow._get_retry_reason(error))

    async def test_image_generation_retries_transient_extension_flow_fetch_error(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.submit_flow_request.side_effect = [
            ExtensionInfrastructureError(
                "Extension script failed at flow_fetch: TypeError: Failed to fetch",
                phase="flow_fetch",
            ),
            {
                "ok": True,
                "status": 200,
                "text": '{"media": [{"name": "media-1"}]}',
                "fingerprint": {"user_agent": "Mozilla/5.0 Chrome/140.0.0.0"},
            },
        ]
        attempt_trace = {}

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ):
            cfg.captcha_method = "extension"
            cfg.flow_image_request_timeout = 10
            cfg.flow_image_timeout_retry_count = 1
            cfg.flow_image_timeout_retry_delay = 0
            cfg.flow_image_timeout_use_media_proxy_fallback = False
            cfg.flow_image_prefer_media_proxy = False

            result = await flow._make_image_generation_request(
                url="https://aisandbox-pa.googleapis.com/v1/projects/project-1/flowMedia:batchGenerateImages",
                json_data={
                    "clientContext": {
                        "recaptchaContext": {"token": "__FLOW2API_EXTENSION_TOKEN__"},
                        "projectId": "project-1",
                    }
                },
                at="at-token",
                attempt_trace=attempt_trace,
                project_id="project-1",
                token_id=1,
            )

        self.assertEqual(result["media"][0]["name"], "media-1")
        self.assertEqual(service.submit_flow_request.await_count, 2)
        self.assertEqual(len(attempt_trace["http_attempts"]), 2)
        self.assertFalse(attempt_trace["http_attempts"][0]["timeout_error"])
        self.assertTrue(attempt_trace["http_attempts"][0]["retryable_error"])

    async def test_flow_request_validation_error_is_not_retried(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.submit_flow_request.side_effect = ExtensionInfrastructureError(
            "Extension script failed at flow_request_validation: invalid_flow_url",
            phase="flow_request_validation",
        )

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ):
            cfg.captcha_method = "extension"
            cfg.flow_image_request_timeout = 10
            cfg.flow_image_timeout_retry_count = 2
            cfg.flow_image_timeout_retry_delay = 0
            cfg.flow_image_timeout_use_media_proxy_fallback = False
            cfg.flow_image_prefer_media_proxy = False

            with self.assertRaises(ExtensionInfrastructureError):
                await flow._make_image_generation_request(
                    url="https://aisandbox-pa.googleapis.com/v1/projects/project-1/flowMedia:batchGenerateImages",
                    json_data={"clientContext": {"projectId": "project-1"}},
                    at="at-token",
                    project_id="project-1",
                    token_id=1,
                )

        service.submit_flow_request.assert_awaited_once()

    async def test_generate_image_retries_after_inner_flow_fetch_retries_are_exhausted(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.submit_flow_request.side_effect = [
            ExtensionInfrastructureError(
                "Extension script failed at flow_fetch: NetworkError when attempting to fetch resource",
                phase="flow_fetch",
            ),
            {
                "ok": True,
                "status": 200,
                "text": '{"media": [{"name": "media-2"}]}',
                "fingerprint": {"user_agent": "Mozilla/5.0 Chrome/140.0.0.0"},
            },
        ]

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ), patch("src.services.flow_client.asyncio.sleep", new=AsyncMock()):
            cfg.captcha_method = "extension"
            cfg.flow_max_retries = 2
            cfg.flow_image_request_timeout = 10
            cfg.flow_image_timeout_retry_count = 0
            cfg.flow_image_timeout_retry_delay = 0
            cfg.flow_image_timeout_use_media_proxy_fallback = False
            cfg.flow_image_prefer_media_proxy = False

            result, session_id, perf_trace = await flow.generate_image(
                at="at-token",
                project_id="project-1",
                prompt="A test image",
                model_name="GEM_PIX",
                aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                token_id=1,
            )

        self.assertEqual(result["media"][0]["name"], "media-2")
        self.assertTrue(session_id)
        self.assertEqual(service.submit_flow_request.await_count, 2)
        self.assertEqual(perf_trace["final_success_attempt"], 2)
        self.assertEqual(len(perf_trace["generation_attempts"]), 2)

    async def test_image_generation_uses_browser_submission_in_extension_mode(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.submit_flow_request.return_value = {
            "ok": True,
            "status": 200,
            "text": '{"media": [{"name": "media-1"}]}',
            "fingerprint": {"user_agent": "Mozilla/5.0 Chrome/140.0.0.0"},
        }

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ):
            cfg.captcha_method = "extension"
            cfg.flow_image_request_timeout = 10
            cfg.flow_image_timeout_retry_count = 0
            cfg.flow_image_timeout_retry_delay = 0
            cfg.flow_image_timeout_use_media_proxy_fallback = False
            cfg.flow_image_prefer_media_proxy = False

            result = await flow._make_image_generation_request(
                url="https://aisandbox-pa.googleapis.com/v1/projects/project-1/flowMedia:batchGenerateImages",
                json_data={
                    "clientContext": {
                        "recaptchaContext": {"token": "__FLOW2API_EXTENSION_TOKEN__"},
                        "projectId": "project-1",
                    }
                },
                at="at-token",
                project_id="project-1",
                token_id=1,
            )

        self.assertEqual(result["media"][0]["name"], "media-1")
        service.submit_flow_request.assert_awaited_once()
        submitted = service.submit_flow_request.await_args.kwargs
        self.assertEqual(submitted["project_id"], "project-1")
        self.assertEqual(submitted["at_token"], "at-token")

    def test_extension_runtime_protocol_and_diagnostics_are_present(self):
        from pathlib import Path

        background = (Path(__file__).parents[1] / "extension" / "background.js").read_text()
        options = (Path(__file__).parents[1] / "extension" / "options.html").read_text()
        options_js = (Path(__file__).parents[1] / "extension" / "options.js").read_text()

        self.assertIn('data.type === "get_token" || data.type === "submit_flow_request"', background)
        self.assertIn("normalizeInjectedResult", background)
        self.assertIn("empty_recaptcha_token", background)
        self.assertIn("phase:", background)
        self.assertIn('message.type !== "reconnect"', background)
        self.assertIn('id="runtimeStatus"', options)
        self.assertIn('id="runtimeLogs"', options)
        self.assertIn('chrome.storage.onChanged.addListener', options_js)

    def test_extension_reconnect_uses_exponential_backoff_and_single_scheduler(self):
        from pathlib import Path

        background = (Path(__file__).parents[1] / "extension" / "background.js").read_text()

        self.assertIn(
            "const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 32000];",
            background,
        )
        self.assertIn("function scheduleReconnect", background)
        self.assertIn("const retryIndex = Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1);", background)
        self.assertIn("const CONNECTION_ATTEMPT_TIMEOUT_MS = 10000;", background)
        self.assertIn("socket.readyState !== WebSocket.CONNECTING", background)
        self.assertIn("WebSocket 连接尝试超时", background)
        self.assertIn("connectionWatchdog = null;", background)
        self.assertIn("reconnectAttempt = 0;", background)
        self.assertIn("scheduleReconnect();", background)
        self.assertIn('scheduleReconnect("重新连接失败")', background)
        self.assertNotIn("Reconnecting in 2s", background)

        close_handler = background.split("socket.onclose = async () => {", 1)[1].split(
            "socket.onerror = async (e) => {", 1
        )[0]
        self.assertEqual(close_handler.count("scheduleReconnect("), 1)

    def test_extension_manifest_is_incremented_for_protocol_fix(self):
        import json
        from pathlib import Path

        manifest = json.loads((Path(__file__).parents[1] / "extension" / "manifest.json").read_text())
        self.assertEqual(manifest["version"], "1.2.8")
        self.assertIn("cookies", manifest["permissions"])

    def test_flow_fetch_in_progress_is_retryable(self):
        flow = FlowClient(proxy_manager=None)
        self.assertTrue(
            flow._is_retryable_extension_flow_fetch_error(
                "Extension script failed at flow_fetch: flow_fetch_in_progress"
            )
        )
        self.assertTrue(
            flow._is_retryable_image_request_error(
                "Extension script failed at flow_fetch: flow_fetch_in_progress"
            )
        )

    def test_image_request_timeout_default_is_raised(self):
        import re
        from pathlib import Path

        from src.core.config import config as live_config

        # 未在 setting.toml 覆盖时应落到新的默认值，而不再是 40s。
        self.assertGreaterEqual(live_config.flow_image_request_timeout, 90)

        root = Path(__file__).parents[1]
        example = (root / "config" / "setting_example.toml").read_text()
        example_match = re.search(r"image_request_timeout\s*=\s*(\d+)", example)
        self.assertIsNotNone(example_match)
        self.assertGreaterEqual(int(example_match.group(1)), 90)

    async def test_retry_reuses_same_batch_id_to_claim_in_flight_result(self):
        flow = FlowClient(proxy_manager=None)
        service = AsyncMock()
        service.submit_flow_request.side_effect = [
            ExtensionInfrastructureError(
                "Extension script failed at flow_fetch: flow_fetch_in_progress",
                phase="flow_fetch",
            ),
            {
                "ok": True,
                "status": 200,
                "text": '{"media": [{"name": "media-claimed"}]}',
                "fingerprint": {"user_agent": "Mozilla/5.0 Chrome/140.0.0.0"},
            },
        ]

        with patch("src.services.flow_client.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=service),
        ):
            cfg.captcha_method = "extension"
            cfg.flow_image_request_timeout = 90
            cfg.flow_image_timeout_retry_count = 1
            cfg.flow_image_timeout_retry_delay = 0
            cfg.flow_image_timeout_use_media_proxy_fallback = False
            cfg.flow_image_prefer_media_proxy = False

            result = await flow._make_image_generation_request(
                url="https://aisandbox-pa.googleapis.com/v1/projects/p1/flowMedia:batchGenerateImages",
                json_data={"clientContext": {"projectId": "p1"}},
                at="at-token",
                project_id="p1",
                token_id=1,
                batch_id="batch-stable-1",
            )

        self.assertEqual(result["media"][0]["name"], "media-claimed")
        self.assertEqual(service.submit_flow_request.await_count, 2)
        sent_batch_ids = {
            call.kwargs["batch_id"] for call in service.submit_flow_request.await_args_list
        }
        self.assertEqual(sent_batch_ids, {"batch-stable-1"})

    async def test_refresh_session_token_dispatches_websocket_request(self):
        service = ExtensionCaptchaService()
        websocket = AsyncMock()
        connection = type(
            "Connection",
            (),
            {"websocket": websocket, "route_key": "route-st", "client_label": "test"},
        )()
        service.active_connections = [connection]
        service._select_connection = lambda route_key: connection
        service._resolve_route_key = AsyncMock(return_value="route-st")

        async def send_text(payload):
            request = __import__("json").loads(payload)
            self.assertEqual(request["type"], "refresh_session_token")
            self.assertEqual(request["route_key"], "route-st")
            self.assertEqual(request["old_st"], "old-st-value")
            service.pending_requests[request["req_id"]][0].set_result({
                "status": "success",
                "session_token": "new-st-123",
                "access_token": "new-at-456",
                "expires": "2026-09-05T12:00:00Z",
            })

        websocket.send_text.side_effect = send_text
        result = await service.refresh_session_token(token_id=1, old_st="old-st-value")
        self.assertEqual(result["session_token"], "new-st-123")
        self.assertEqual(result["access_token"], "new-at-456")
        self.assertEqual(result["expires"], "2026-09-05T12:00:00Z")

    async def test_refresh_session_token_handles_extension_error(self):
        service = ExtensionCaptchaService()
        websocket = AsyncMock()
        connection = type(
            "Connection",
            (),
            {"websocket": websocket, "route_key": "", "client_label": "test"},
        )()
        service.active_connections = [connection]
        service._select_connection = lambda route_key: connection
        service._resolve_route_key = AsyncMock(return_value="")

        async def send_text(payload):
            request = __import__("json").loads(payload)
            service.pending_requests[request["req_id"]][0].set_result({
                "status": "error",
                "error": "Google session expired",
            })

        websocket.send_text.side_effect = send_text
        with self.assertRaises(ExtensionInfrastructureError) as ctx:
            await service.refresh_session_token(token_id=1, old_st="old-st")
        self.assertIn("Google session expired", str(ctx.exception))

    async def test_token_manager_try_refresh_st_via_extension(self):
        from src.services.token_manager import TokenManager
        from src.core.models import Token

        db = AsyncMock()
        flow = AsyncMock()
        token_mgr = TokenManager(db=db, flow_client=flow)
        mock_ext_service = AsyncMock()
        mock_ext_service.has_connection_for_token.return_value = (True, "xxx")
        mock_ext_service.refresh_session_token.return_value = {
            "session_token": "refreshed-st-from-ext",
            "access_token": "refreshed-at-from-ext",
            "expires": "2026-09-05T15:00:00Z",
        }

        token = Token(
            id=1,
            email="xxx@gmail.com",
            st="old-st",
            at="old-at",
            protocol_mode="session",
            extension_route_key="xxx",
        )

        with patch("src.services.token_manager.config") as cfg, patch(
            "src.services.browser_captcha_extension.ExtensionCaptchaService.get_instance",
            new=AsyncMock(return_value=mock_ext_service),
        ):
            cfg.captcha_method = "extension"
            new_st = await token_mgr._try_refresh_st(1, token)

        self.assertEqual(new_st, "refreshed-st-from-ext")
        db.update_token.assert_awaited()
        update_kwargs = db.update_token.await_args.kwargs
        self.assertEqual(update_kwargs["st"], "refreshed-st-from-ext")
        self.assertEqual(update_kwargs["at"], "refreshed-at-from-ext")

    def test_extension_runtime_handles_refresh_session_token_code(self):
        from pathlib import Path

        background = (Path(__file__).parents[1] / "extension" / "background.js").read_text()
        self.assertIn('data.type === "refresh_session_token"', background)
        self.assertIn("handleRefreshSessionToken", background)
        self.assertIn("getNextAuthSessionCookie", background)
        self.assertIn("triggerLabsNextAuthSignIn", background)

    def test_extension_runtime_supports_concurrency_and_anti_throttling(self):
        from pathlib import Path

        background = (Path(__file__).parents[1] / "extension" / "background.js").read_text()
        self.assertIn("acquireTaskSlot", background)
        self.assertIn("releaseTaskSlot", background)
        self.assertIn("autoDiscardable", background)
        self.assertIn("maxConcurrency", background)
        self.assertIn("getActiveSocket", background)

    async def test_extension_handles_ping_with_pong(self):
        import json
        service = ExtensionCaptchaService()
        ws = AsyncMock()
        await service.handle_message(ws, json.dumps({"type": "ping"}))
        ws.send_text.assert_awaited_once()
        sent = json.loads(ws.send_text.await_args[0][0])
        self.assertEqual(sent.get("type"), "pong")

    async def test_extension_disconnect_fails_pending_requests_promptly(self):
        import asyncio
        from src.services.browser_captcha_extension import ExtensionConnection

        service = ExtensionCaptchaService()
        ws = AsyncMock()
        conn = ExtensionConnection(websocket=ws, route_key="test-route")
        service.active_connections = [conn]

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        service.pending_requests["req-123"] = (future, ws)

        service.disconnect(ws)

        self.assertNotIn(conn, service.active_connections)
        self.assertTrue(future.done())
        with self.assertRaises(ExtensionInfrastructureError) as cm:
            future.result()
        self.assertEqual(cm.exception.phase, "connection")
        self.assertIn("disconnected", str(cm.exception))

    async def test_extension_reconnect_delivers_response_to_pending_request(self):
        import asyncio
        import json
        from src.services.browser_captcha_extension import ExtensionConnection

        service = ExtensionCaptchaService()
        old_ws = AsyncMock()
        new_ws = AsyncMock()
        old_conn = ExtensionConnection(websocket=old_ws, route_key="test-route")
        new_conn = ExtensionConnection(websocket=new_ws, route_key="test-route")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        service.pending_requests["req-456"] = (future, old_ws)

        # Old websocket disconnected and new websocket connected
        service.active_connections = [new_conn]

        # Response arrives over the reconnected websocket
        payload = {
            "req_id": "req-456",
            "status": "success",
            "token": "token-after-reconnect",
        }
        await service.handle_message(new_ws, json.dumps(payload))

        self.assertTrue(future.done())
        result = future.result()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["token"], "token-after-reconnect")

    def test_extension_includes_anti_poison_protection(self):
        import json
        from pathlib import Path

        ext_dir = Path(__file__).parents[1] / "extension"
        manifest = json.loads((ext_dir / "manifest.json").read_text())
        content_scripts = manifest.get("content_scripts", [])
        protection_script = next((s for s in content_scripts if "inject_protection.js" in s.get("js", [])), None)
        self.assertIsNotNone(protection_script)
        self.assertEqual(protection_script.get("run_at"), "document_start")
        self.assertEqual(protection_script.get("world"), "MAIN")

        protection_content = (ext_dir / "inject_protection.js").read_text()
        self.assertIn("extension_hijack", protection_content)
        self.assertIn("enable_recaptcha_execute_closure_wrap", protection_content)
        self.assertIn("default_AiSandboxAngularFrontend", protection_content)
        self.assertIn("grecaptcha", protection_content)

        background = (ext_dir / "background.js").read_text()
        self.assertIn("registerProtectionScript", background)
        self.assertIn("recaptcha_poisoned", background)
        self.assertIn("extension_hijack", background)


if __name__ == "__main__":
    unittest.main()
