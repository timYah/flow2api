import unittest
from unittest.mock import AsyncMock, patch
import uuid

from src.core.models import Token
from src.services.flow_client import FlowClient
from src.services.token_manager import TokenManager


class CreateProjectDeprecatedFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_flow_client_create_project_fallback_on_404(self):
        client = FlowClient(proxy_manager=AsyncMock())
        # Mock _make_request raising HTTP Error 404 with deprecation message
        with patch.object(
            client,
            "_make_request",
            AsyncMock(
                side_effect=Exception(
                    "Flow API request failed: HTTP Error 404: "
                    '{"error":{"json":{"message":"Flow RPCs have been deprecated and disabled. Flow has migrated to https://flow.google.com."}}}'
                )
            ),
        ):
            project_id = await client.create_project("dummy-st", "test-project")

        # Must return a valid UUID string instead of raising
        self.assertIsInstance(project_id, str)
        parsed_uuid = uuid.UUID(project_id)
        self.assertEqual(str(parsed_uuid), project_id)

    async def test_token_manager_add_token_succeeds_even_when_create_project_fails(self):
        db = AsyncMock()
        db.get_token_by_st.return_value = None
        db.add_token.return_value = 1
        db.add_project.return_value = 101

        flow = AsyncMock()
        flow.st_to_at.return_value = {
            "access_token": "ya29.test-at",
            "expires": "2026-10-03T07:00:00Z",
            "user": {"email": "test@example.com", "name": "test"},
        }
        flow.get_credits.return_value = {"credits": 100, "userPaygateTier": "PAYGATE_TIER_ONE"}
        # create_project raises 404
        flow.create_project.side_effect = Exception("Flow API request failed: HTTP Error 404")

        manager = TokenManager(db=db, flow_client=flow)

        token = await manager.add_token(st="dummy-st")

        self.assertIsNotNone(token)
        self.assertEqual(token.email, "test@example.com")
        self.assertEqual(token.at, "ya29.test-at")
        self.assertTrue(bool(token.current_project_id))
        # Ensure project was added to db
        db.add_project.assert_awaited()
