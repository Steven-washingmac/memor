"""历史记录 API"""

from fastapi import APIRouter, HTTPException

from backend.api.auth import _call_totoro
from backend.database import get_current_user
from backend.models import ApiResponse

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/calendar", response_model=ApiResponse)
async def get_calendar_data() -> ApiResponse:
    """
    获取本学期所有跑步记录，按日期聚合，用于日历视图。

    返回格式：
    {
        "termId": "...",
        "termName": "2025-2026学年第二学期",
        "records": { "2026-03-01": {...}, ... }
    }
    """
    user = get_current_user()
    if not user:
        raise HTTPException(401, "未登录")

    try:
        # Step 1: 获取学期列表，找到当前学期
        terms_result = await _call_totoro("platform/course/getSchoolTerm", {
            "schoolId": user.get("schoolId", ""),
            "token": user["token"],
        })
        terms = terms_result.get("data", []) or terms_result.get("termList", [])
        if not terms:
            return ApiResponse(success=False, message="无学期数据")

        current_term = None
        for t in terms:
            if t.get("isCurrent") == "1":
                current_term = t
                break
        if not current_term:
            current_term = terms[-1]

        term_id = current_term["termId"]
        term_name = current_term.get("termName", "")

        # Step 2: 获取学期所有月份
        months_result = await _call_totoro("platform/course/getSchoolMonthByTerm", {
            "schoolId": user.get("schoolId", ""),
            "stuNumber": user["stuNumber"],
            "token": user["token"],
            "termId": term_id,
        })
        months = months_result.get("monthList", [])
        if not months:
            return ApiResponse(success=False, message="无月份数据")

        # Step 3: 逐月获取跑步记录
        all_records: dict[str, dict | None] = {}
        for month in months:
            month_id = month["monthId"]
            try:
                arch_result = await _call_totoro("sunrun/getSunrunArch", {
                    "campusId": user.get("campusId", ""),
                    "schoolId": user.get("schoolId", ""),
                    "stuNumber": user["stuNumber"],
                    "token": user["token"],
                    "runType": "0",
                    "monthId": month_id,
                    "termId": term_id,
                })
                run_list = arch_result.get("data", [])
                for record in run_list:
                    run_time = record.get("runTime", "")
                    if run_time:
                        run_date = run_time.split(" ")[0]
                        all_records[run_date] = {
                            "mileage": record.get("mileage", "0"),
                            "usedTime": record.get("usedTime", ""),
                            "status": record.get("status", "1"),
                            "scoreId": record.get("scoreId", ""),
                        }
            except Exception:
                continue

        return ApiResponse(success=True, data={
            "termId": term_id,
            "termName": term_name,
            "records": all_records,
        })

    except Exception as e:
        return ApiResponse(success=False, message=f"获取日历数据失败: {e}")


@router.get("/tasks", response_model=ApiResponse)
async def get_run_task_history() -> ApiResponse:
    """获取本服务提交的跑步任务历史"""
    from backend.database import get_run_tasks

    user = get_current_user()
    if not user:
        raise HTTPException(401, "未登录")

    tasks = get_run_tasks(user["stuNumber"])
    return ApiResponse(success=True, data=tasks)
