import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import WebSocket

from ..core.logger import debug_logger


class ExtensionInfrastructureError(RuntimeError):
    """Chrome 扩展连接或脚本执行失败，不代表账号/token 无效。"""

    count_as_token_error = False

    def __init__(self, message: str, *, phase: Optional[str] = None):
        super().__init__(message)
        self.phase = phase or "unknown"


@dataclass
class ExtensionConnection:
    websocket: WebSocket
    route_key: str = ""
    client_label: str = ""
    connected_at: float = field(default_factory=time.time)


class ExtensionCaptchaService:
    _instance: Optional["ExtensionCaptchaService"] = None
    _lock = asyncio.Lock()

    def __init__(self, db=None):
        self.db = db
        self.active_connections: list[ExtensionConnection] = []
        self.pending_requests: dict[str, tuple[asyncio.Future, WebSocket]] = {}
        self._on_register_callbacks: list = []
        self._last_fingerprint: Optional[Dict[str, Any]] = None

    @classmethod
    def get_instance_sync(cls, db=None) -> Optional["ExtensionCaptchaService"]:
        if cls._instance is None and db is not None:
            cls._instance = cls(db=db)
        elif db is not None and cls._instance is not None and cls._instance.db is None:
            cls._instance.db = db
        return cls._instance

    @classmethod
    async def get_instance(cls, db=None) -> "ExtensionCaptchaService":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db=db)
        elif db is not None and cls._instance.db is None:
            cls._instance.db = db
        return cls._instance

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        conn = ExtensionConnection(
            websocket=websocket,
            route_key=(websocket.query_params.get("route_key") or "").strip(),
            client_label=(websocket.query_params.get("client_label") or "").strip(),
        )
        self.active_connections.append(conn)
        debug_logger.log_info(
            f"[Extension Captcha] Client connected. Total: {len(self.active_connections)}, "
            f"route_key={conn.route_key or '-'}, label={conn.client_label or '-'}"
        )

    def disconnect(self, websocket: WebSocket):
        conn = self._find_connection(websocket)
        route_key = conn.route_key if conn else ""
        for conn_item in list(self.active_connections):
            if conn_item.websocket is websocket:
                self.active_connections.remove(conn_item)
                debug_logger.log_info(
                    f"[Extension Captcha] Client disconnected. Total: {len(self.active_connections)}, "
                    f"route_key={conn_item.route_key or '-'}, label={conn_item.client_label or '-'}"
                )
                break

        # 如果与该连接相关的 route_key 没有其它活动连接，立即让该连接上的所有未决请求失败，
        # 避免调用方持续等待超时。
        has_alt_conn = (
            any(c.route_key == route_key for c in self.active_connections)
            if route_key
            else bool(self.active_connections)
        )
        if not has_alt_conn:
            for req_id, (future, owner_ws) in list(self.pending_requests.items()):
                if owner_ws is websocket and not future.done():
                    future.set_exception(
                        ExtensionInfrastructureError(
                            "Chrome Extension disconnected while waiting for response",
                            phase="connection",
                        )
                    )

    def _find_connection(self, websocket: WebSocket) -> Optional[ExtensionConnection]:
        for conn in self.active_connections:
            if conn.websocket is websocket:
                return conn
        return None

    def _select_connection(self, route_key: str) -> Optional[ExtensionConnection]:
        normalized_key = (route_key or "").strip()
        if normalized_key:
            for conn in self.active_connections:
                if conn.route_key == normalized_key:
                    return conn
            # 容错降级：如果当前只有唯一一个在线扩展连接且未绑定指定路由键，允许借用该连接
            if len(self.active_connections) == 1 and not (self.active_connections[0].route_key or "").strip():
                debug_logger.log_info(
                    f"[Extension Captcha] 目标路由 '{normalized_key}' 借用唯一在线的默认扩展连接"
                )
                return self.active_connections[0]
            return None
        for conn in self.active_connections:
            if not conn.route_key:
                return conn
        # 若未指定路由键但仅有唯一扩展在线，直接复用
        if len(self.active_connections) == 1:
            return self.active_connections[0]
        return None

    def _describe_routes(self) -> str:
        labels = []
        for conn in self.active_connections:
            label = conn.route_key or "(empty)"
            if conn.client_label:
                label = f"{label}:{conn.client_label}"
            labels.append(label)
        return ", ".join(labels)

    def describe_routes(self) -> str:
        return self._describe_routes()

    async def _send_ack(self, websocket: WebSocket, payload: Dict[str, Any]):
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    async def _resolve_route_key(self, token_id: Optional[int]) -> str:
        if not token_id or not self.db:
            return ""
        try:
            token = await self.db.get_token(token_id)
            if token and token.extension_route_key:
                return token.extension_route_key.strip()
        except Exception as e:
            debug_logger.log_warning(f"[Extension Captcha] Failed to resolve route key for token {token_id}: {e}")
        return ""

    def _has_connection_for_route_key(self, route_key: str) -> bool:
        return self._select_connection(route_key) is not None

    async def has_connection_for_token(self, token_id: Optional[int], token_obj=None) -> tuple[bool, str]:
        if token_obj is not None and getattr(token_obj, "extension_route_key", None):
            route_key = str(token_obj.extension_route_key).strip()
        else:
            route_key = await self._resolve_route_key(token_id)
        return self._has_connection_for_route_key(route_key), route_key

    async def handle_message(self, websocket: WebSocket, data: str):
        try:
            payload = json.loads(data)
            message_type = payload.get("type")

            if message_type == "register":
                conn = self._find_connection(websocket)
                if conn:
                    conn.route_key = (payload.get("route_key") or conn.route_key or "").strip()
                    conn.client_label = (payload.get("client_label") or conn.client_label or "").strip()
                    debug_logger.log_info(
                        f"[Extension Captcha] Client registered route_key={conn.route_key or '-'}, "
                        f"label={conn.client_label or '-'}"
                    )
                    await self._send_ack(
                        websocket,
                        {
                            "type": "register_ack",
                            "route_key": conn.route_key,
                            "client_label": conn.client_label,
                        },
                    )
                    for cb in list(self._on_register_callbacks):
                        try:
                            res = cb(conn.route_key)
                            if asyncio.iscoroutine(res):
                                asyncio.create_task(res)
                        except Exception as cb_err:
                            debug_logger.log_warning(f"[Extension Captcha] on_register callback error: {cb_err}")
                return

            if message_type == "sync_session_token":
                session_token = str(payload.get("session_token") or "").strip()
                route_key = str(payload.get("route_key") or (self._find_connection(websocket).route_key if self._find_connection(websocket) else "") or "").strip()
                email = str(payload.get("email") or "").strip()
                access_token = payload.get("access_token")
                expires = payload.get("expires")
                if session_token and self.db:
                    all_tokens = await self.db.get_all_tokens()
                    for tok in (all_tokens or []):
                        tok_route = (getattr(tok, "extension_route_key", "") or "").strip()
                        tok_email = (getattr(tok, "email", "") or "").strip()
                        match = False
                        if route_key and tok_route and tok_route == route_key:
                            match = True
                        elif email and tok_email and tok_email.lower() == email.lower():
                            match = True
                        if match:
                            update_kwargs = {"st": session_token}
                            if access_token:
                                update_kwargs["at"] = str(access_token)
                            if expires:
                                try:
                                    update_kwargs["at_expires"] = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                                except Exception:
                                    pass
                            await self.db.update_token(tok.id, **update_kwargs)
                            if not tok.is_active:
                                await self.db.update_token(tok.id, is_active=1)
                            debug_logger.log_info(
                                f"[Extension Captcha] 扩展主动同步 Session Token: token_id={tok.id} ({tok.email}), 自动更新并已启用"
                            )
                return

            if message_type == "ping":
                await self._send_ack(websocket, {"type": "pong"})
                return

            req_id = payload.get("req_id")
            if req_id and req_id in self.pending_requests:
                future, owner_websocket = self.pending_requests[req_id]
                conn = self._find_connection(websocket)
                owner_conn = self._find_connection(owner_websocket)
                is_owner = (websocket is owner_websocket)
                is_valid_reconnect = (
                    conn is not None
                    and (
                        owner_conn is None
                        or conn.route_key == owner_conn.route_key
                        or len(self.active_connections) == 1
                    )
                )
                if not (is_owner or is_valid_reconnect):
                    debug_logger.log_warning(
                        f"[Extension Captcha] Ignoring response from non-matching connection: {req_id}"
                    )
                    return
                if not future.done():
                    future.set_result(payload)
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Error handling message: {e}")

    async def get_token(
        self,
        project_id: str,
        action: str = "IMAGE_GENERATION",
        timeout: int = 20,
        token_id: Optional[int] = None,
    ) -> Optional[str]:
        bundle = await self.get_token_bundle(
            project_id=project_id,
            action=action,
            timeout=timeout,
            token_id=token_id,
        )
        return str(bundle.get("token") or "").strip() or None if bundle else None

    async def get_token_bundle(
        self,
        project_id: str,
        action: str = "IMAGE_GENERATION",
        timeout: int = 20,
        token_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取 token 及生成 token 时的 Chrome 运行环境。"""
        if not self.active_connections:
            debug_logger.log_warning("[Extension Captcha] No active extension connections available.")
            raise ExtensionInfrastructureError(
                "Chrome Extension not connected or Google Labs tab not open.",
                phase="connection",
            )

        route_key = await self._resolve_route_key(token_id)
        conn = self._select_connection(route_key)
        if conn is None:
            available = self._describe_routes() or "none"
            raise ExtensionInfrastructureError(
                f"No Chrome Extension connection matches token_id={token_id} route_key='{route_key}'. "
                f"Available route keys: {available}",
                phase="connection",
            )

        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = (future, conn.websocket)

        request_data = {
            "type": "get_token",
            "req_id": req_id,
            "action": action,
            "project_id": project_id,
            "route_key": route_key,
        }

        try:
            debug_logger.log_info(
                f"[Extension Captcha] Dispatching token request via route_key={route_key or '-'}, "
                f"label={conn.client_label or '-'}, project_id={project_id}, action={action}"
            )
            await conn.websocket.send_text(json.dumps(request_data))
            result = await asyncio.wait_for(future, timeout=max(60, int(timeout) + 35))

            if not isinstance(result, dict):
                raise ExtensionInfrastructureError(
                    "Chrome Extension returned an invalid response payload",
                    phase="response",
                )

            if result.get("status") == "success":
                token = str(result.get("token") or "").strip()
                if not token:
                    raise ExtensionInfrastructureError(
                        "Chrome Extension returned a success response without a token",
                        phase="response",
                    )
                bundle = {
                    "token": token,
                    "fingerprint": result.get("fingerprint") if isinstance(result.get("fingerprint"), dict) else {},
                    "session_cookies": result.get("session_cookies") if isinstance(result.get("session_cookies"), dict) else {},
                    "page_url": str(result.get("page_url") or "").strip(),
                }
                if bundle["fingerprint"]:
                    self._last_fingerprint = dict(bundle["fingerprint"])
                return bundle

            error_msg = result.get("error")
            debug_logger.log_error(f"[Extension Captcha] Error from extension: {error_msg}")
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
            phase = str(result.get("phase") or diagnostics.get("phase") or "script")
            error_code = str(result.get("error_code") or "extension_script_failed")
            raise ExtensionInfrastructureError(
                f"{error_code}: {error_msg or 'Chrome Extension token request failed'}",
                phase=phase,
            )

        except asyncio.TimeoutError as exc:
            debug_logger.log_error(f"[Extension Captcha] Timeout waiting for token (req_id: {req_id})")
            raise ExtensionInfrastructureError(
                "Timed out waiting for Chrome Extension token request",
                phase="timeout",
            ) from exc
        except ExtensionInfrastructureError:
            raise
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Communication error: {e}")
            raise ExtensionInfrastructureError(
                f"Chrome Extension communication failed: {e}",
                phase="communication",
            ) from e
        finally:
            self.pending_requests.pop(req_id, None)

    async def submit_flow_request(
        self,
        project_id: str,
        action: str,
        url: str,
        at_token: str,
        json_data: Dict[str, Any],
        timeout: int,
        token_id: Optional[int] = None,
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """在扩展所控制的 Chrome Flow 页面内取码并提交请求。

        reCAPTCHA token 可能同时关联浏览器运行时、Cookie、IP 和网络指纹。
        因此扩展模式不能只把 token 交给 Python，再由 curl_cffi 代发 Flow 请求。
        """
        if not self.active_connections:
            raise ExtensionInfrastructureError(
                "Chrome Extension not connected or Google Labs tab not open.",
                phase="connection",
            )

        route_key = await self._resolve_route_key(token_id)
        conn = self._select_connection(route_key)
        if conn is None:
            available = self._describe_routes() or "none"
            raise ExtensionInfrastructureError(
                f"No Chrome Extension connection matches token_id={token_id} route_key='{route_key}'. "
                f"Available route keys: {available}",
                phase="connection",
            )

        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = (future, conn.websocket)
        request_data = {
            "type": "submit_flow_request",
            "req_id": req_id,
            "action": action,
            "project_id": project_id,
            "route_key": route_key,
            "flow_request": {
                "url": url,
                "at_token": at_token,
                "json_data": json_data,
                "timeout_ms": max(5000, int(timeout * 1000)),
                "batch_id": batch_id or "",
            },
        }

        try:
            debug_logger.log_info(
                f"[Extension Captcha] Dispatching in-browser Flow request via route_key={route_key or '-'}, "
                f"label={conn.client_label or '-'}, project_id={project_id}, action={action}"
            )
            await conn.websocket.send_text(json.dumps(request_data))
            # Extension execution includes loading the Flow project page,
            # reCAPTCHA evaluation, and the in-browser Flow request.
            result = await asyncio.wait_for(future, timeout=max(90, int(timeout) + 45))
            if not isinstance(result, dict):
                raise ExtensionInfrastructureError(
                    "Chrome Extension returned an invalid response payload",
                    phase="response",
                )
            if result.get("status") != "success":
                diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
                phase = str(result.get("phase") or diagnostics.get("phase") or "script")
                error_code = str(result.get("error_code") or "extension_script_failed")
                raise ExtensionInfrastructureError(
                    f"{error_code}: {result.get('error') or 'Chrome Extension Flow request failed'}",
                    phase=phase,
                )

            flow_response = result.get("flow_response")
            if not isinstance(flow_response, dict):
                raise ExtensionInfrastructureError(
                    "Chrome Extension returned an invalid Flow response",
                    phase="flow_response",
                )
            fp = result.get("fingerprint")
            if isinstance(fp, dict) and fp:
                self._last_fingerprint = dict(fp)
            flow_response["fingerprint"] = (
                fp if isinstance(fp, dict) else {}
            )
            flow_response["session_cookies"] = (
                result.get("session_cookies")
                if isinstance(result.get("session_cookies"), dict)
                else {}
            )
            flow_response["page_url"] = str(result.get("page_url") or "").strip()
            return flow_response
        except asyncio.TimeoutError as exc:
            raise ExtensionInfrastructureError(
                "Timed out waiting for Chrome Extension Flow request",
                phase="timeout",
            ) from exc
        except ExtensionInfrastructureError:
            raise
        except Exception as exc:
            raise ExtensionInfrastructureError(
                f"Chrome Extension communication failed: {exc}",
                phase="communication",
            ) from exc
        finally:
            self.pending_requests.pop(req_id, None)

    def get_last_fingerprint(self) -> Optional[Dict[str, Any]]:
        """获取最近一次成功的扩展浏览器指纹快照。"""
        if not self._last_fingerprint:
            return None
        return dict(self._last_fingerprint)

    async def report_flow_error(self, project_id: str, error_reason: str, error_message: str = ""):
        _ = project_id, error_message
        debug_logger.log_warning(f"[Extension Captcha] Flow error reported (ignoring): {error_reason}")

    async def refresh_session_token(
        self,
        token_id: Optional[int] = None,
        old_st: Optional[str] = None,
        timeout: int = 60,
        route_key: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """通过 Chrome 扩展刷新或获取 Session Token (__Secure-next-auth.session-token)。"""
        if not self.active_connections:
            raise ExtensionInfrastructureError(
                "Chrome Extension not connected.",
                phase="connection",
            )

        if not route_key:
            route_key = await self._resolve_route_key(token_id)
        conn = self._select_connection(route_key)
        if conn is None:
            available = self._describe_routes() or "none"
            raise ExtensionInfrastructureError(
                f"No Chrome Extension connection matches token_id={token_id} route_key='{route_key}'. "
                f"Available route keys: {available}",
                phase="connection",
            )

        if not email and token_id and self.db:
            try:
                token_obj = await self.db.get_token(token_id)
                if token_obj and token_obj.email:
                    email = token_obj.email.strip()
            except Exception:
                pass

        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = (future, conn.websocket)

        request_data = {
            "type": "refresh_session_token",
            "req_id": req_id,
            "token_id": token_id,
            "old_st": old_st or "",
            "email": email,
            "route_key": route_key,
        }

        try:
            debug_logger.log_info(
                f"[Extension Captcha] Dispatching refresh_session_token via route_key={route_key or '-'}, "
                f"label={conn.client_label or '-'}, token_id={token_id}"
            )
            await conn.websocket.send_text(json.dumps(request_data))
            result = await asyncio.wait_for(future, timeout=max(30, int(timeout)))

            if not isinstance(result, dict):
                raise ExtensionInfrastructureError(
                    "Chrome Extension returned an invalid response payload for session refresh",
                    phase="response",
                )

            if result.get("status") == "success":
                session_token = str(result.get("session_token") or "").strip()
                if not session_token:
                    raise ExtensionInfrastructureError(
                        "Chrome Extension returned success but missing session_token",
                        phase="response",
                    )
                return {
                    "session_token": session_token,
                    "access_token": str(result.get("access_token") or "").strip() or None,
                    "expires": str(result.get("expires") or "").strip() or None,
                }

            error_msg = result.get("error") or "Chrome Extension session refresh failed"
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
            phase = str(result.get("phase") or diagnostics.get("phase") or "script")
            raise ExtensionInfrastructureError(error_msg, phase=phase)

        except asyncio.TimeoutError as exc:
            raise ExtensionInfrastructureError(
                "Timed out waiting for Chrome Extension session refresh",
                phase="timeout",
            ) from exc
        except ExtensionInfrastructureError:
            raise
        except Exception as e:
            raise ExtensionInfrastructureError(
                f"Chrome Extension communication failed: {e}",
                phase="communication",
            ) from e
        finally:
            self.pending_requests.pop(req_id, None)
