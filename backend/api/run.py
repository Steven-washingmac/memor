"""跑步 API - 获取任务、生成轨迹、提交记录"""

import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.api.auth import _call_totoro
from backend.database import get_current_user, save_run_task
from backend.models import ApiResponse, SubmitRunRequest
from backend.route_generator import (
    generate_route_for_point_id,
    get_route_filename,
    get_run_time_window,
    load_route,
    save_route,
)

router = APIRouter(prefix="/run", tags=["run"])
logger = logging.getLogger("run")


# ============================================================
# 获取今日跑步任务（路线、距离要求等）
# ============================================================
@router.get("/paper", response_model=ApiResponse)
async def get_run_paper() -> ApiResponse:
    """获取跑步任务（路线列表、距离、时间要求等）"""
    user = get_current_user()
    if not user:
        raise HTTPException(401, "未登录")

    try:
        result = await _call_totoro("sunrun/getSunrunPaper", {
            "campusId": user.get("campusId", ""),
            "schoolId": user.get("schoolId", ""),
            "stuNumber": user["stuNumber"],
            "token": user["token"],
        })
        return ApiResponse(success=True, data=result)
    except Exception as e:
        return ApiResponse(success=False, message=f"获取任务失败: {e}")


# ============================================================
# 生成跑步轨迹
# ============================================================
@router.post("/generate", response_model=ApiResponse)
async def generate_route(data: dict) -> ApiResponse:
    """
    根据路线 ID 生成 GPS 轨迹。

    请求体：
    {
        "routeId": "sunrunLine-20230208000001",
        "distance": 3.2,
        "runDate": "2026-03-01",
        "startTime": "06:00",
        "durationMin": 15
    }
    """
    user = get_current_user()
    if not user:
        raise HTTPException(401, "未登录")

    route_id = data.get("routeId")
    distance = data.get("distance", 3.2)
    run_date = data.get("runDate")
    start_time = data.get("startTime")
    duration_min = data.get("durationMin")

    if not route_id:
        return ApiResponse(success=False, message="缺少 routeId")

    try:
        route_data = generate_route_for_point_id(
            point_id=route_id,
            distance_km=float(distance),
            run_date=run_date,
            start_time_str=start_time,
            duration_min=duration_min,
        )

        filename = get_route_filename(
            run_date or datetime.now().strftime("%Y-%m-%d"), route_id
        )
        file_path = save_route(route_data, filename)

        logger.info(f"轨迹已生成: {file_path}, 点数={route_data['pointCount']}")

        return ApiResponse(success=True, data={
            "filePath": file_path,
            "routeInfo": route_data["routeInfo"],
            "pointCount": route_data["pointCount"],
            "distance": route_data["distance"],
            "startTime": route_data["startTime"],
            "endTime": route_data["endTime"],
            "usedTime": route_data["usedTime"],
            "avgSpeed": route_data["avgSpeed"],
        })
    except Exception as e:
        return ApiResponse(success=False, message=f"生成轨迹失败: {e}")


# ============================================================
# 提交跑步记录
# ============================================================
def _generate_mac(stu_number: str) -> str:
    """根据学号生成设备 MAC 地址"""
    hash_hex = hashlib.sha256(stu_number.encode()).hexdigest()[:12]
    return ":".join(hash_hex[i:i + 2] for i in range(0, 12, 2))


@router.post("/submit", response_model=ApiResponse)
async def submit_run(req: SubmitRunRequest) -> ApiResponse:
    """
    提交跑步记录（包含 GPS 轨迹）。

    流程：
    1. getRunBegin → 通知服务器开始跑步
    2. sunRunExercises → 提交跑步汇总数据
    3. sunRunExercisesDetail → 提交 GPS 轨迹
    """
    user = get_current_user()
    if not user:
        raise HTTPException(401, "未登录")

    token = user["token"]
    stu_number = user["stuNumber"]
    school_id = user.get("schoolId", "")
    campus_id = user.get("campusId", "")

    # 读取已生成的轨迹
    run_date = req.runDate or datetime.now().strftime("%Y-%m-%d")
    route_id = req.routeId
    if not route_id:
        return ApiResponse(success=False, message="缺少 routeId")
    filename = get_route_filename(run_date, route_id)

    try:
        route_data = load_route(filename)
    except FileNotFoundError:
        return ApiResponse(
            success=False,
            message=f"轨迹文件不存在: {filename}。请先调用 /api/run/generate 生成轨迹。",
        )

    mock_route = route_data["mockRoute"]
    start_time_str = route_data["startTime"]
    end_time_str = route_data["endTime"]
    used_time = route_data["usedTime"]
    avg_speed = route_data["avgSpeed"]
    evaluate_date = route_data["evaluateDate"]
    target_distance = route_data["targetDistance"]
    route_info = route_data["routeInfo"]

    # 判断 ifLocalSubmit：当日+规定时间段内 → "0"，否则 → "1"
    try:
        run_date_obj = datetime.strptime(run_date, "%Y-%m-%d").date()
        today = datetime.now().date()
        now_time = datetime.now().time()
        win_s, win_e = get_run_time_window()
        ws = datetime.strptime(win_s, "%H:%M").time()
        we = datetime.strptime(win_e, "%H:%M").time()
        if_local_submit = "0" if (run_date_obj == today and ws <= now_time <= we) else "1"
    except Exception:
        if_local_submit = "1"

    # Step 1: getRunBegin
    try:
        await _call_totoro("sunrun/getRunBegin", {
            "campusId": campus_id, "schoolId": school_id,
            "stuNumber": stu_number, "token": token,
        })
        logger.info("getRunBegin OK")
    except Exception as e:
        logger.warning(f"getRunBegin warning: {e}")

    # Step 2: sunRunExercises（汇总）
    summary = {
        "LocalSubmitReason": "", "avgSpeed": avg_speed, "baseStation": "",
        "endTime": end_time_str, "evaluateDate": evaluate_date, "fitDegree": "1",
        "flag": "1", "headImage": "", "ifLocalSubmit": if_local_submit,
        "km": target_distance, "mac": _generate_mac(stu_number),
        "phoneInfo": "$CN11/iPhone15,4/17.4.1", "phoneNumber": "",
        "pointList": "", "routeId": route_info["pointId"], "runType": "0",
        "sensorString": "", "startTime": start_time_str,
        "steps": route_data["steps"], "stuNumber": stu_number,
        "taskId": route_info["taskId"], "token": token,
        "usedTime": used_time, "version": "1.2.14",
        "warnFlag": "0", "warnType": "", "faceData": "",
    }

    try:
        result = await _call_totoro("platform/recrecord/sunRunExercises", summary)
    except Exception as e:
        return ApiResponse(success=False, message=f"提交汇总失败: {e}")

    scantron_id = result.get("scantronId")
    if not scantron_id:
        return ApiResponse(success=False, message=f"未获取到 scantronId: {result}")

    # Step 3: sunRunExercisesDetail（GPS 轨迹，明文 JSON）
    try:
        await _call_totoro("platform/recrecord/sunRunExercisesDetail", {
            "pointList": mock_route, "scantronId": scantron_id,
            "stuNumber": stu_number, "token": token,
        }, encrypted=False)
    except Exception as e:
        return ApiResponse(
            success=False, message=f"提交轨迹失败: {e}",
            data={"scantronId": scantron_id}
        )

    # 保存任务记录
    save_run_task(
        stu_number=stu_number, run_date=run_date, route_id=route_id,
        distance=target_distance, used_time=used_time, avg_speed=avg_speed,
        scantron_id=scantron_id, status="success"
    )

    logger.info(f"跑步提交成功: {stu_number} on {run_date}, scantronId={scantron_id}")

    return ApiResponse(success=True, data={
        "scantronId": scantron_id,
        "distance": target_distance,
        "usedTime": used_time,
        "avgSpeed": avg_speed,
        "points": len(mock_route),
    })
