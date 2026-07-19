"""Pydantic 数据模型"""

from typing import Any
from pydantic import BaseModel, Field


# ============================================================
# Session 相关
# ============================================================
class TotoroSession(BaseModel):
    """龙猫校园用户会话"""
    stuNumber: str = Field(..., description="学号")
    stuName: str | None = Field(None, description="姓名")
    schoolId: str | None = Field(None, description="学校ID")
    schoolName: str | None = Field(None, description="学校名称")
    campusId: str | None = Field(None, description="校区ID")
    campusName: str | None = Field(None, description="校区名称")
    collegeName: str | None = Field(None, description="学院名称")
    phoneNumber: str | None = Field(None, description="手机号")
    token: str = Field(..., description="登录令牌")
    serverPath: str = Field("https://app.xtotoro.com")
    path: str = Field("https://app.xtotoro.com")
    newsUrl: str | None = None
    useUrl: str | None = None
    registerUrl: str | None = None


class SessionWrapper(BaseModel):
    """Session 文件包装"""
    success: bool = True
    session: TotoroSession
    issuedAt: float | None = None


# ============================================================
# 微信登录
# ============================================================
class QrCodeResponse(BaseModel):
    uuid: str
    imgUrl: str


class ScanStatusResponse(BaseModel):
    message: str | None
    code: str | None
    scanned: bool = False


class LoginRequest(BaseModel):
    code: str


class LoginResponse(BaseModel):
    success: bool
    session: TotoroSession | None = None
    message: str | None = None


# ============================================================
# 跑步相关
# ============================================================
class Point(BaseModel):
    longitude: str
    latitude: str


class RouteInfo(BaseModel):
    taskId: str
    pointId: str
    pointName: str


class SubmitRunRequest(BaseModel):
    routeId: str | None = None
    runDate: str | None = Field(None, description="跑步日期 YYYY-MM-DD")
    startTime: str | None = Field(None, description="开始时间 HH:MM")
    durationMin: int | None = Field(None, description="跑步时长（分钟）")


class RunRecordDetail(BaseModel):
    """跑步记录详情"""
    scoreId: str | None = None
    runDate: str | None = None
    mileage: str | None = None
    usedTime: str | None = None
    avgSpeed: str | None = None
    status: str | None = None


# ============================================================
# 通用 API 响应
# ============================================================
class ApiResponse(BaseModel):
    success: bool
    data: Any | None = None
    message: str | None = None
