"""dashboard/auth.py — P2-2.2 登录与角色（D2 + Q2a）

身份复用不另造：
- 普通用户登录 = ``user_name`` + **dashboard token**。token 在 ``xskill connect
  --name`` 注册成功时由 server 发放（存 clients.dashboard_token，client 侧打印
  一次），跨设备同 name 同 token。
- admin = ``dashboard.admins`` 名单（user_name 列表）+ 单独强口令
  ``dashboard.admin_password``。口令为空 = admin 登录关闭（显式，非默认开）。

会话 = HMAC-SHA256 签名 cookie（无服务端 session 表）：
``base64url(json{u,role,exp}) . base64url(hmac)``。secret 随机生成落
``~/.xskill/dashboard_secret.json``（0600），server 重启会话仍有效。

角色enforcement走 FastAPI dependency（``require_user`` / ``require_admin``），
写路由本身只在 serve 内置形态挂载（D4）——依赖是第二道闸，不是唯一闸。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

logger = logging.getLogger("xskill.dashboard.auth")

SESSION_COOKIE = "xskill_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# secret 持久化
# ---------------------------------------------------------------------------

def ensure_dashboard_secret(path: Path | str) -> str:
    """读取（或生成并 0600 落盘）dashboard 会话签名 secret。"""
    path = Path(path)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sec = data.get("secret")
            if isinstance(sec, str) and sec:
                return sec
        except (json.JSONDecodeError, OSError):
            logger.warning("dashboard secret 文件损坏,重新生成: %s", path)
    sec = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"secret": sec}), encoding="utf-8")
    path.chmod(0o600)
    return sec


# ---------------------------------------------------------------------------
# 会话签名
# ---------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SessionSigner:
    def __init__(self, secret: str):
        self._key = secret.encode()

    def sign(self, user: str, role: str, *, ttl: int = SESSION_TTL_SECONDS) -> str:
        payload = json.dumps(
            {"u": user, "r": role, "exp": int(time.time()) + ttl},
            separators=(",", ":"),
        ).encode()
        sig = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64e(payload)}.{_b64e(sig)}"

    def verify(self, token: str) -> Optional[dict]:
        """合法且未过期 → {"user":…, "role":…}；否则 None。"""
        try:
            p64, s64 = token.split(".", 1)
            payload = _b64d(p64)
            sig = _b64d(s64)
        except (ValueError, TypeError):
            return None
        want = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, want):
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if int(data.get("exp", 0)) < time.time():
            return None
        return {"user": data.get("u", ""), "role": data.get("r", "")}


# ---------------------------------------------------------------------------
# 模块级 auth 上下文（mount 时注入，端点依赖读取）
# ---------------------------------------------------------------------------

class _AuthContext:
    def __init__(self):
        self.signer: SessionSigner | None = None
        self.admins: list[str] = []
        self.admin_password: str = ""
        # 零参 callable → ClientRegistry | None。用 provider 而非实例:
        # mount_dashboard 在 create_app 时跑,而 ClientRegistry 在 app startup
        # 才创建(team ctx 初始化),登录时才解引用。
        self.registry_provider = None


_ctx = _AuthContext()


def configure_auth(*, secret: str, admins: list[str], admin_password: str,
                   registry_provider=None) -> None:
    """serve 启动时调用一次。registry_provider 返回 None → 普通用户登录
    不可用（standalone 无 connect 身份，仅 admin 口令登录）。"""
    _ctx.signer = SessionSigner(secret)
    _ctx.admins = [a.strip() for a in admins if a and a.strip()]
    _ctx.admin_password = admin_password or ""
    _ctx.registry_provider = registry_provider


def _identity_from_request(request: Request) -> Optional[dict]:
    if _ctx.signer is None:
        return None
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw:
        return None
    return _ctx.signer.verify(raw)


def require_user(request: Request) -> dict:
    """登录即可（user 或 admin）。未登录 401。"""
    ident = _identity_from_request(request)
    if ident is None:
        raise HTTPException(status_code=401, detail="login required")
    return ident


def require_admin(request: Request) -> dict:
    """仅 admin。未登录 401，非 admin 403。"""
    ident = _identity_from_request(request)
    if ident is None:
        raise HTTPException(status_code=401, detail="login required")
    if ident.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return ident


# ---------------------------------------------------------------------------
# 登录端点
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    user_name: str
    secret: str  # 普通用户=dashboard token；admin=dashboard.admin_password


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/dashboard")

    @router.post("/login")
    def login(req: LoginRequest, response: Response):
        if _ctx.signer is None:
            raise HTTPException(status_code=503, detail="auth not configured")
        user = req.user_name.strip()
        if not user or not req.secret:
            raise HTTPException(status_code=400, detail="user_name/secret required")
        role = _resolve_role(user, req.secret)
        if role is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        response.set_cookie(
            SESSION_COOKIE, _ctx.signer.sign(user, role),
            max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
        )
        return {"user": user, "role": role}

    @router.post("/logout")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @router.get("/me")
    def me(request: Request):
        ident = _identity_from_request(request)
        if ident is None:
            raise HTTPException(status_code=401, detail="not logged in")
        return ident

    return router


def _resolve_role(user: str, secret: str) -> Optional[str]:
    """显式两分支判定（非 fallback 链）：
    ① user ∈ admins 且 admin_password 非空且匹配 → admin
    ② client_registry 里该 user_name 的 dashboard token 匹配 → user
    """
    if (user in _ctx.admins and _ctx.admin_password
            and hmac.compare_digest(secret, _ctx.admin_password)):
        return "admin"
    reg = _ctx.registry_provider() if _ctx.registry_provider else None
    if reg is not None:
        stored = reg.dashboard_token_for(user)
        if stored and hmac.compare_digest(secret, stored):
            # 命名用户也可能在 admins 名单里,但走 token 登录只给 user 角色
            # ——admin 角色必须出示 admin_password(Q2a"admin 单独强口令")
            return "user"
    return None
