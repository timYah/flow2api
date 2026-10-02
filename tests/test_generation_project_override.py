import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.api import routes
from src.core.models import (
    ChatCompletionRequest,
    ChatMessage,
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
    Project,
    Token,
)
from src.services.generation_handler import GenerationHandler, MODEL_CONFIG
from src.services.load_balancer import LoadBalancer


IMAGE_MODEL = "gemini-3.1-flash-image-landscape"


def _make_token(token_id: int) -> Token:
    return Token(
        id=token_id,
        st=f"st-{token_id}",
        at=f"at-{token_id}",
        email=f"token-{token_id}@example.com",
        user_paygate_tier="PAYGATE_TIER_NOT_PAID",
    )


class ProjectRequestNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_request_normalizes_project_id(self):
        request = ChatCompletionRequest(
            model=IMAGE_MODEL,
            messages=[ChatMessage(role="user", content="draw a cat")],
            project_id="  project-openai  ",
        )

        normalized = await routes._normalize_openai_request(request)

        self.assertEqual(normalized.project_id, "project-openai")

    async def test_openai_gemini_contents_preserve_project_id(self):
        request = ChatCompletionRequest(
            model=IMAGE_MODEL,
            contents=[{"role": "user", "parts": [{"text": "draw a cat"}]}],
            project_id="project-contents",
        )

        normalized = await routes._normalize_openai_request(request)

        self.assertEqual(normalized.project_id, "project-contents")

    async def test_gemini_request_normalizes_project_id(self):
        request = GeminiGenerateContentRequest(
            contents=[
                GeminiContent(role="user", parts=[GeminiPart(text="draw a cat")])
            ],
            project_id="  project-gemini  ",
        )

        normalized = await routes._normalize_gemini_request(IMAGE_MODEL, request)

        self.assertEqual(normalized.project_id, "project-gemini")

    async def test_all_execution_paths_forward_project_id(self):
        class RecordingHandler:
            def __init__(self):
                self.calls = []

            async def handle_generation(self, **kwargs):
                self.calls.append(kwargs)
                yield "{}"

        handler = RecordingHandler()
        normalized = routes.NormalizedGenerationRequest(
            model=IMAGE_MODEL,
            prompt="draw a cat",
            images=[],
            project_id="project-forwarded",
        )

        with patch.object(routes, "generation_handler", handler):
            await routes._collect_non_stream_result(
                model=normalized.model,
                prompt=normalized.prompt,
                images=normalized.images,
                project_id=normalized.project_id,
            )
            _ = [chunk async for chunk in routes._iterate_openai_stream(normalized)]
            _ = [
                chunk
                async for chunk in routes._iterate_gemini_stream(
                    normalized,
                    normalized.model,
                )
            ]

        self.assertEqual(len(handler.calls), 3)
        self.assertTrue(
            all(call["project_id"] == "project-forwarded" for call in handler.calls)
        )


class RequiredTokenSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_token_has_no_fallback_or_round_robin_side_effect(self):
        first_token = _make_token(1)
        required_token = _make_token(2)
        token_manager = types.SimpleNamespace(
            get_active_tokens=AsyncMock(return_value=[first_token, required_token]),
            needs_at_refresh=MagicMock(return_value=False),
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        load_balancer = LoadBalancer(token_manager=token_manager)
        fake_config = types.SimpleNamespace(
            captcha_method="yescaptcha",
            call_logic_mode="polling",
        )

        with patch("src.services.load_balancer.config", fake_config):
            selected = await load_balancer.select_token(
                for_image_generation=True,
                model=IMAGE_MODEL,
                track_pending=True,
                required_token_id=2,
            )

        self.assertEqual(selected.id, 2)
        token_manager.ensure_valid_token.assert_awaited_once_with(required_token)
        self.assertEqual(
            load_balancer._round_robin_state,
            {"image": None, "video": None, "default": None},
        )

    async def test_missing_required_token_does_not_use_another_token(self):
        available_token = _make_token(1)
        token_manager = types.SimpleNamespace(
            get_active_tokens=AsyncMock(return_value=[available_token]),
            needs_at_refresh=MagicMock(return_value=False),
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        load_balancer = LoadBalancer(token_manager=token_manager)

        selected = await load_balancer.select_token(
            for_image_generation=True,
            model=IMAGE_MODEL,
            required_token_id=99,
        )

        self.assertIsNone(selected)
        token_manager.ensure_valid_token.assert_not_awaited()

    async def test_cached_tier_mismatch_is_refreshed_before_filtering(self):
        cached_token = _make_token(1)
        cached_token.user_paygate_tier = "PAYGATE_TIER_NOT_PAID"
        refreshed_token = _make_token(1)
        refreshed_token.user_paygate_tier = "PAYGATE_TIER_ONE"
        token_manager = types.SimpleNamespace(
            get_active_tokens=AsyncMock(return_value=[cached_token]),
            needs_at_refresh=MagicMock(return_value=False),
            ensure_valid_token=AsyncMock(return_value=refreshed_token),
        )
        load_balancer = LoadBalancer(token_manager=token_manager)
        fake_config = types.SimpleNamespace(
            captcha_method="yescaptcha",
            call_logic_mode="default",
        )

        with patch("src.services.load_balancer.config", fake_config):
            selected = await load_balancer.select_token(
                for_image_generation=True,
                model="gemini-3.1-flash-image-square-2k",
                refresh_tier_on_mismatch=True,
            )

        self.assertIs(selected, refreshed_token)
        token_manager.ensure_valid_token.assert_awaited_once_with(cached_token)


class GenerationProjectOverrideTests(unittest.IsolatedAsyncioTestCase):
    def _make_handler(self, project, selected_token):
        handler = GenerationHandler.__new__(GenerationHandler)
        handler.flow_client = types.SimpleNamespace(
            clear_request_fingerprint=MagicMock(),
            prefill_remote_browser_pool=AsyncMock(),
        )
        handler.db = types.SimpleNamespace(
            get_project_by_id=AsyncMock(return_value=project),
            get_project_by_client_id=AsyncMock(return_value=None),
            clear_project_client_id=AsyncMock(),
        )
        handler.load_balancer = types.SimpleNamespace(
            select_token=AsyncMock(return_value=selected_token),
            release_pending=AsyncMock(),
            get_unavailable_reason=AsyncMock(return_value=None),
        )
        handler.token_manager = types.SimpleNamespace(
            ensure_valid_token=AsyncMock(return_value=selected_token),
            ensure_project_exists=AsyncMock(return_value="pool-project"),
            create_client_project=AsyncMock(
                side_effect=lambda token, client_project_id: Project(
                    project_id=f"real-{client_project_id}",
                    client_project_id=client_project_id,
                    token_id=token.id,
                    project_name=client_project_id,
                )
            ),
            record_usage=AsyncMock(),
            record_success=AsyncMock(),
            record_error=AsyncMock(),
        )
        handler._log_request = AsyncMock(return_value=None)
        handler._update_request_log_progress = AsyncMock()

        async def successful_image_generation(*args, **kwargs):
            kwargs["generation_result"]["success"] = True
            yield "generated"

        handler._handle_image_generation = successful_image_generation
        return handler

    async def _collect(self, handler, **kwargs):
        return [
            chunk
            async for chunk in handler.handle_generation(
                model=kwargs.pop("model", IMAGE_MODEL),
                prompt="draw a cat",
                stream=False,
                **kwargs,
            )
        ]

    async def test_registered_project_uses_owner_token_and_skips_pool(self):
        token = _make_token(7)
        project = Project(
            project_id="requested-project",
            token_id=token.id,
            project_name="Requested project",
        )
        handler = self._make_handler(project, token)
        captured = {}

        async def fake_image_generation(
            selected_token,
            project_id,
            model_config,
            prompt,
            images,
            stream,
            **kwargs,
        ):
            captured["token_id"] = selected_token.id
            captured["project_id"] = project_id
            kwargs["generation_result"]["success"] = True
            yield "generated"

        handler._handle_image_generation = fake_image_generation

        chunks = await self._collect(handler, project_id="requested-project")

        self.assertEqual(chunks, ["generated"])
        self.assertEqual(captured, {"token_id": 7, "project_id": "requested-project"})
        handler.load_balancer.select_token.assert_awaited_once()
        self.assertEqual(
            handler.load_balancer.select_token.await_args.kwargs["required_token_id"],
            7,
        )
        handler.token_manager.ensure_project_exists.assert_not_awaited()

    async def test_omitted_project_keeps_pool_selection(self):
        token = _make_token(7)
        handler = self._make_handler(project=None, selected_token=token)
        captured = {}

        async def fake_image_generation(
            selected_token,
            project_id,
            model_config,
            prompt,
            images,
            stream,
            **kwargs,
        ):
            captured["project_id"] = project_id
            kwargs["generation_result"]["success"] = True
            yield "generated"

        handler._handle_image_generation = fake_image_generation

        chunks = await self._collect(handler)

        self.assertEqual(chunks, ["generated"])
        self.assertEqual(captured["project_id"], "pool-project")
        handler.db.get_project_by_id.assert_not_awaited()
        handler.token_manager.ensure_project_exists.assert_awaited_once_with(7)
        self.assertNotIn("required_token_id", handler.load_balancer.select_token.await_args.kwargs)

    async def test_unknown_project_is_created_and_bound_to_selected_token(self):
        token = _make_token(7)
        handler = self._make_handler(project=None, selected_token=token)

        chunks = await self._collect(handler, project_id="new-project")

        self.assertEqual(chunks, ["generated"])
        handler.token_manager.create_client_project.assert_awaited_once_with(token, "new-project")
        handler.token_manager.ensure_project_exists.assert_not_awaited()

    async def test_concurrent_unknown_project_is_created_once(self):
        token = _make_token(7)
        handler = self._make_handler(project=None, selected_token=token)
        created = Project(
            project_id="real-concurrent-project",
            client_project_id="concurrent-project",
            token_id=token.id,
            project_name="concurrent-project",
        )
        lookup_count = 0

        async def lookup_by_alias(alias):
            nonlocal lookup_count
            lookup_count += 1
            if lookup_count > 1:
                return created
            return None

        handler.db.get_project_by_client_id = AsyncMock(side_effect=lookup_by_alias)
        handler.token_manager.create_client_project = AsyncMock(return_value=created)

        first, second = await asyncio.gather(
            self._collect(handler, project_id="concurrent-project"),
            self._collect(handler, project_id="concurrent-project"),
        )

        self.assertEqual(first, ["generated"])
        self.assertEqual(second, ["generated"])
        handler.token_manager.create_client_project.assert_awaited_once()

    async def test_inactive_project_is_replaced_and_alias_rebound(self):
        token = _make_token(7)
        inactive = Project(
            project_id="old-project",
            client_project_id="requested-project",
            token_id=token.id,
            project_name="Inactive project",
            is_active=False,
        )
        handler = self._make_handler(project=inactive, selected_token=token)

        await self._collect(handler, project_id="requested-project")

        handler.db.clear_project_client_id.assert_awaited_once_with("old-project")
        handler.token_manager.create_client_project.assert_awaited_once_with(
            token,
            "requested-project",
        )

    async def test_missing_project_with_no_available_token_returns_503(self):
        handler = self._make_handler(project=None, selected_token=None)

        chunks = await self._collect(handler, project_id="new-project")
        payload = json.loads(chunks[-1])

        self.assertEqual(payload["error"]["status_code"], 503)
        handler.token_manager.create_client_project.assert_not_awaited()

    async def test_missing_project_reports_model_account_requirement(self):
        handler = self._make_handler(project=None, selected_token=None)
        handler.load_balancer.get_unavailable_reason = AsyncMock(
            return_value="当前模型需要 Pro 账号，但没有可用的 Pro 账号: gemini-3.1-flash-image-square-2k"
        )

        chunks = await self._collect(
            handler,
            model="gemini-3.1-flash-image-square-2k",
            project_id="new-project",
        )
        payload = json.loads(chunks[-1])

        self.assertEqual(payload["error"]["status_code"], 503)
        self.assertIn("需要 Pro 账号", payload["error"]["message"])
        self.assertIn("project_id: new-project", payload["error"]["message"])
        handler.token_manager.create_client_project.assert_not_awaited()

    async def test_unavailable_owner_does_not_fallback_and_returns_503(self):
        project = Project(
            project_id="requested-project",
            token_id=7,
            project_name="Requested project",
        )
        handler = self._make_handler(project=project, selected_token=None)

        chunks = await self._collect(handler, project_id="requested-project")
        payload = json.loads(chunks[-1])

        self.assertEqual(payload["error"]["status_code"], 503)
        self.assertEqual(
            handler.load_balancer.select_token.await_args.kwargs["required_token_id"],
            7,
        )

    async def test_video_model_with_project_id_returns_400(self):
        handler = self._make_handler(project=None, selected_token=_make_token(7))
        video_model = next(
            model
            for model, model_config in MODEL_CONFIG.items()
            if model_config["type"] == "video"
        )

        chunks = await self._collect(
            handler,
            model=video_model,
            project_id="requested-project",
        )
        payload = json.loads(chunks[-1])

        self.assertEqual(payload["error"]["status_code"], 400)
        self.assertIn("仅支持图片生成", payload["error"]["message"])
        handler.db.get_project_by_id.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
