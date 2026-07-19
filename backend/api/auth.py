"""微信登录 API - 二维码获取、扫码检测、登录"""

import asyncio
import re
import time
import logging

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.config import (
    API_PREFIX, BASE_URL, DEFAULT_HEADERS,
    WECHAT_QR_URL, WECHAT_SCAN_URL,
)
from backend.crypto import rsa_encrypt, try_decrypt_response
from backend.models import (
    LoginRequest, LoginResponse, QrCodeResponse, ScanStatusResponse, TotoroSession,
)
from backend.database import save_user, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("auth")

# 内存中暂存扫码状态
_scan_cache: dict[str, dict] = {}


async def _call_totoro(endpoint: str, payload: dict, encrypted: bool = True) -> dict:
    """调用龙猫校园官方 API（加密通信）"""
    url = f"{BASE_URL}{API_PREFIX}/{endpoint}"
    headers = dict(DEFAULT_HEADERS)

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        if encrypted:
            body = rsa_encrypt(payload)
            headers["Content-Type"] = "application/json; charset=utf-8"
            resp = await client.post(url, content=body, headers=headers)
        else:
            headers["Content-Type"] = "application/json"
            resp = await client.post(url, json=payload, headers=headers)

        resp.raise_for_status()
        return try_decrypt_response(resp.text)


# ============================================================
# Step 1: 获取微信登录二维码
# ============================================================
@router.get("/qr", response_model=QrCodeResponse)
async def get_wechat_qr() -> QrCodeResponse:
    """
    获取微信登录二维码。

    直接请求龙猫校园 APP 绑定的微信开放平台授权页面，
    解析出 uuid 和二维码图片 URL。
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 8_0 like Mac OS X) "
                "AppleWebKit/600.1.4 (KHTML, like Gecko) "
                "Mobile/12A365 MicroMessenger/5.4.1 NetType/WIFI WebView/doc"
            ),
        }
        try:
            resp = await client.get(WECHAT_QR_URL, headers=headers)
            resp.raise_for_status()
            html = resp.text

            uuid_match = re.search(r'uuid:\s*"([^"]+)"', html)
            if not uuid_match:
                raise HTTPException(502, "无法解析二维码 UUID")
            uuid = uuid_match.group(1)

            img_match = re.search(r'auth_qrcode"\s+src="([^"]+)"', html)
            if not img_match:
                raise HTTPException(502, "无法解析二维码图片 URL")
            img_url = img_match.group(1)
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                img_url = "https://open.weixin.qq.com" + img_url

            _scan_cache[uuid] = {"status": "pending", "code": None}
            logger.info(f"二维码生成成功, uuid={uuid[:20]}...")
            return QrCodeResponse(uuid=uuid, imgUrl=img_url)

        except httpx.HTTPStatusError as e:
            raise HTTPException(502, f"微信服务器错误: {e.response.status_code}")
        except Exception as e:
            raise HTTPException(500, f"获取二维码失败: {str(e)}")


# ============================================================
# Step 2: 查询扫码状态（轮询 + SSE 推送）
# ============================================================
@router.get("/scan/{uuid}")
async def check_scan_status(uuid: str) -> ScanStatusResponse:
    """查询微信扫码状态"""
    cache = _scan_cache.get(uuid)
    if not cache:
        return ScanStatusResponse(message="二维码已过期，请重新获取", code=None)

    if cache.get("code"):
        return ScanStatusResponse(message=None, code=cache["code"], scanned=True)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        try:
            url = f"{WECHAT_SCAN_URL}?uuid={uuid}&f=url"
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
            code_match = re.search(r"://oauth\?code=(\w+)&", text)
            if code_match:
                code = code_match.group(1)
                cache["code"] = code
                cache["status"] = "scanned"
                logger.info(f"用户扫码成功, code={code[:20]}...")
                return ScanStatusResponse(message=None, code=code, scanned=True)
            return ScanStatusResponse(message="等待扫码...", code=None, scanned=False)
        except Exception:
            return ScanStatusResponse(message="等待扫码...", code=None, scanned=False)


@router.get("/scan/{uuid}/sse")
async def scan_status_sse(uuid: str):
    """SSE 实时推送扫码状态（前端自动感知扫码）"""
    async def event_generator():
        for _ in range(300):  # 最多 5 分钟
            cache = _scan_cache.get(uuid)
            if not cache:
                yield 'event: error\ndata: {"message": "二维码已过期"}\n\n'
                return
            if cache.get("code"):
                yield f'event: scanned\ndata: {{"code": "{cache["code"]}"}}\n\n'
                return

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
                    url = f"{WECHAT_SCAN_URL}?uuid={uuid}&f=url"
                    resp = await c.get(url)
                    text = resp.text
                    code_match = re.search(r"://oauth\?code=(\w+)&", text)
                    if code_match:
                        cache["code"] = code_match.group(1)
                        yield f'event: scanned\ndata: {{"code": "{cache["code"]}"}}\n\n'
                        return
            except Exception:
                pass

            yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(1)

        yield 'event: timeout\ndata: {"message": "扫码超时"}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ============================================================
# Step 3: 用 code 登录龙猫校园
# ============================================================
@router.post("/login", response_model=LoginResponse)
async def login_with_code(req: LoginRequest) -> LoginResponse:
    """
    使用微信 code 完成龙猫校园登录。

    流程：
    1. getLesseeServer(code) → 获取 token
    2. login(token) → 获取用户信息
    3. 调用辅助 API（模拟正常客户端行为）
    4. 保存 session 到数据库
    """
    code = req.code

    # Step 1: code → token
    try:
        lessee_resp = await _call_totoro("platform/serverlist/getLesseeServer", {"code": code})
    except Exception as e:
        return LoginResponse(success=False, message=f"获取 token 失败: {e}")

    token = lessee_resp.get("token")
    if not token:
        return LoginResponse(success=False, message=f"微信授权失败: {lessee_resp.get('message', '未知错误')}")

    # Step 2: token → 用户信息
    try:
        login_resp = await _call_totoro("platform/login/login", {
            "code": "", "latitude": "", "loginWay": "", "longitude": "",
            "password": "", "phoneNumber": "", "token": token,
        })
    except Exception as e:
        return LoginResponse(success=False, message=f"登录失败: {e}")

    # Step 3: 构建 Session
    try:
        session = TotoroSession(
            stuNumber=str(login_resp.get("stuNumber", "")),
            stuName=login_resp.get("stuName"),
            schoolId=str(login_resp.get("schoolId", "")) if login_resp.get("schoolId") else None,
            schoolName=login_resp.get("schoolName"),
            campusId=login_resp.get("campusId"),
            campusName=login_resp.get("campusName"),
            collegeName=login_resp.get("collegeName"),
            phoneNumber=login_resp.get("phoneNumber"),
            token=token,
            serverPath=login_resp.get("serverPath", "https://app.xtotoro.com"),
            path=login_resp.get("path", "https://app.xtotoro.com"),
            newsUrl=login_resp.get("newsUrl"),
            useUrl=login_resp.get("useUrl"),
            registerUrl=login_resp.get("registerUrl"),
        )
    except Exception as e:
        return LoginResponse(success=False, message=f"解析用户信息失败: {e}")

    # Step 4: 调用辅助 API（让服务器认为我们是正常客户端）
    try:
        basic = {
            "campusId": session.campusId or "",
            "schoolId": session.schoolId or "",
            "stuNumber": session.stuNumber,
            "token": session.token,
        }
        await _call_totoro("platform/login/getAppFrontPage", basic)
        await _call_totoro("platform/serverlist/getAppSlogan", basic)
        await _call_totoro("platform/serverlist/updateAppVersion", {
            **basic, "version": "1.2.14", "deviceType": "2"
        })
        await _call_totoro("platform/serverlist/getAppNotice", {**basic, "version": ""})
    except Exception:
        pass  # 辅助 API 失败不影响登录

    # Step 5: 保存到数据库
    session_dict = session.model_dump(mode="json")
    save_user(session_dict)
    logger.info(f"用户登录成功: {session.stuName} ({session.stuNumber})")

    return LoginResponse(success=True, session=session)
