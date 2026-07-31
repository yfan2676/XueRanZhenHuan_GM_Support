"""共享口令保护（私人局用）。

定位：挡住公网上乱扫 URL 的机器人和路人，不是防定向攻击，也不防同局玩家互相偷看身份。

- 口令来自环境变量 `GM_PASSWORD`（可写在项目根目录的 `.env` 里，该文件不入 git）。
- 未设置口令时中间件完全关闭 —— `uv run main.py` 局域网自用不受影响。
- 校验通过后写 Cookie，每台设备只需输一次。
"""

import asyncio
import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, RedirectResponse

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
COOKIE_NAME = "gm_auth"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 天，一个赛季不用重输
LOGIN_PATH = "/login"

# 暴力破解限流（进程内存，重启即清空 —— 私人局够用）
FAIL_WINDOW = 900        # 15 分钟滑动窗口
FAIL_LOCKOUT = 20        # 窗口内失败超过此数直接 429
_failures: dict[str, list[float]] = {}


def load_dotenv(path: Path = ENV_FILE) -> None:
    """把 .env 读进 os.environ（不覆盖已存在的变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _token(password: str) -> str:
    """Cookie 里存派生值而不是口令原文。"""
    return hashlib.sha256(f"gm-auth:{password}".encode()).hexdigest()


def _client_ip(request) -> str:
    # cloudflared / 反代会带上真实来源；直连时退回 socket 地址
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_https(request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return proto == "https" if proto else request.url.scheme == "https"


def _safe_next(raw: str | None) -> str:
    """只允许站内相对路径，避免被当成开放重定向。"""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return "/"
    return raw


def _record_failure(ip: str) -> int:
    now = time.monotonic()
    hits = [t for t in _failures.get(ip, []) if now - t < FAIL_WINDOW]
    hits.append(now)
    _failures[ip] = hits
    return len(hits)


def _failure_count(ip: str) -> int:
    now = time.monotonic()
    hits = [t for t in _failures.get(ip, []) if now - t < FAIL_WINDOW]
    if hits:
        _failures[ip] = hits
    else:
        _failures.pop(ip, None)
    return len(hits)


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>血战甄嬛传 · 入场</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #1a1214; color: #efe3d0; min-height: 100vh;
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }}
  .box {{
    background: #261a1d; border: 1px solid #3a2a2e; border-radius: 10px;
    padding: 32px 28px; width: 100%; max-width: 360px; text-align: center;
  }}
  h1 {{ color: #d4a95a; font-size: 20px; letter-spacing: 3px; margin-bottom: 6px; }}
  p.sub {{ color: #a89684; font-size: 13px; margin-bottom: 22px; }}
  input {{
    width: 100%; padding: 12px 14px; font-size: 16px; border-radius: 6px;
    border: 1px solid #3a2a2e; background: #322226; color: #efe3d0; margin-bottom: 14px;
  }}
  input:focus {{ outline: none; border-color: #9a7b42; }}
  button {{
    width: 100%; padding: 12px; font-size: 16px; border: none; border-radius: 6px;
    background: #d4a95a; color: #1a1214; font-weight: 600; cursor: pointer;
  }}
  button:hover {{ background: #e0b96f; }}
  .err {{ color: #e05563; font-size: 13px; margin-bottom: 14px; min-height: 18px; }}
</style>
</head>
<body>
  <div class="box">
    <h1>血战甄嬛传</h1>
    <p class="sub">私人局 · 请输入入场口令</p>
    <div class="err">{error}</div>
    <form method="post" action="{action}">
      <input type="password" name="password" placeholder="口令" autofocus
             autocomplete="current-password" required>
      <button type="submit">进入</button>
    </form>
  </div>
</body>
</html>"""


def _login_page(next_path: str, error: str = "", status: int = 200) -> HTMLResponse:
    action = f"{LOGIN_PATH}?next={quote(next_path, safe='/')}"
    html = LOGIN_PAGE.format(error=error, action=action)
    return HTMLResponse(html, status_code=status)


class PasswordMiddleware(BaseHTTPMiddleware):
    """在所有路由（含 StaticFiles 挂载）之前校验共享口令。"""

    def __init__(self, app, password: str):
        super().__init__(app)
        self.password = password
        self.token = _token(password)

    async def dispatch(self, request, call_next):
        if request.url.path == LOGIN_PATH:
            return await self._handle_login(request)

        cookie = request.cookies.get(COOKIE_NAME, "")
        if hmac.compare_digest(cookie, self.token):
            return await call_next(request)

        # 未登录：页面请求给登录页，接口请求给 401（避免前端把 HTML 当 JSON 解析）
        if request.headers.get("accept", "").find("text/html") >= 0:
            return _login_page(_safe_next(request.url.path), status=200)
        return HTMLResponse('{"detail":"未登录"}', status_code=401,
                            media_type="application/json")

    async def _handle_login(self, request):
        next_path = _safe_next(request.query_params.get("next"))

        if request.method != "POST":
            return _login_page(next_path)

        ip = _client_ip(request)
        if _failure_count(ip) >= FAIL_LOCKOUT:
            return _login_page(next_path, "尝试过于频繁，请稍后再试", status=429)

        body = (await request.body()).decode("utf-8", "replace")
        submitted = parse_qs(body).get("password", [""])[0]

        if not hmac.compare_digest(submitted, self.password):
            fails = _record_failure(ip)
            await asyncio.sleep(min(0.5 * fails, 5.0))  # 递增延迟，拖慢撞库
            return _login_page(next_path, "口令不对", status=401)

        _failures.pop(ip, None)
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            COOKIE_NAME, self.token,
            max_age=COOKIE_MAX_AGE, httponly=True,
            secure=_is_https(request), samesite="lax", path="/",
        )
        return response


def install(app) -> None:
    """有口令就装中间件，没有就原样放行（局域网自用）。"""
    load_dotenv()
    password = os.environ.get("GM_PASSWORD", "").strip()
    if not password:
        print("[auth] 未设置 GM_PASSWORD，口令保护已关闭（仅适合局域网自用）")
        return
    app.add_middleware(PasswordMiddleware, password=password)
    print("[auth] 口令保护已启用")
