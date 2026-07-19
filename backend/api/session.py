"""Session 管理 API"""

from fastapi import APIRouter

from backend.database import get_current_user, delete_user
from backend.models import ApiResponse

router = APIRouter(prefix="/session", tags=["session"])


@router.get("", response_model=ApiResponse)
async def get_session() -> ApiResponse:
    """获取当前登录用户"""
    user = get_current_user()
    if not user:
        return ApiResponse(success=False, message="未登录", data=None)
    return ApiResponse(success=True, data=user)


@router.delete("", response_model=ApiResponse)
async def clear_session() -> ApiResponse:
    """退出登录（清除当前用户 session）"""
    user = get_current_user()
    if user:
        delete_user(user["stuNumber"])
    return ApiResponse(success=True, message="已退出登录")


@router.get("/check", response_model=ApiResponse)
async def check_login() -> ApiResponse:
    """检查登录状态"""
    user = get_current_user()
    if not user:
        return ApiResponse(success=False, message="未登录")
    return ApiResponse(success=True, data={
        "stuName": user.get("stuName"),
        "stuNumber": user.get("stuNumber"),
        "schoolName": user.get("schoolName"),
    })
