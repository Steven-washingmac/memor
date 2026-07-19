"""GPS 跑步轨迹生成器 - 基于 map.json 路线数据生成逼真的跑步轨迹"""

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# map.json 路径
_MAP_JSON_PATH = Path(__file__).parent.parent / "map.json"
_map_data: dict[str, Any] | None = None


def _load_map_data() -> dict[str, Any]:
    """加载 map.json 路线数据（带缓存）"""
    global _map_data
    if _map_data is not None:
        return _map_data
    if not _MAP_JSON_PATH.exists():
        raise FileNotFoundError(f"map.json 不存在: {_MAP_JSON_PATH}")
    with open(_MAP_JSON_PATH, "r", encoding="utf-8") as f:
        _map_data = json.load(f)
    return _map_data


def get_run_time_window() -> tuple[str, str]:
    """获取跑步时间窗口，默认 06:00-23:00"""
    try:
        data = _load_map_data()
        return data.get("startTime", "06:00"), data.get("endTime", "23:00")
    except FileNotFoundError:
        return "06:00", "23:00"


def _normal_random(mean: float, std: float) -> float:
    """正态分布随机数（3σ 范围裁剪）"""
    while True:
        u = random.random() * 2 - 1.0
        v = random.random() * 2 - 1.0
        w = u * u + v * v
        if w == 0 or w >= 1.0:
            continue
        c = math.sqrt((-2 * math.log(w)) / w)
        result = mean + u * c * std
        if mean - 3 * std <= result <= mean + 3 * std:
            return result


def _distance_between_points(p1: list[float], p2: list[float]) -> float:
    """计算两点间距离（米）- 与龙猫校园 APP 算法一致"""
    d1 = 0.0174532925194329
    d2, d3 = float(p1[0]), float(p1[1])
    d4, d5 = float(p2[0]), float(p2[1])
    d2 *= d1; d3 *= d1; d4 *= d1; d5 *= d1
    d6, d7 = math.sin(d2), math.sin(d3)
    d8, d9 = math.cos(d2), math.cos(d3)
    d10, d11 = math.sin(d4), math.sin(d5)
    d12, d13 = math.cos(d4), math.cos(d5)
    d14 = math.sqrt(
        (d9 * d8 - d13 * d12) ** 2 +
        (d9 * d6 - d13 * d10) ** 2 +
        (d7 - d11) ** 2
    )
    return math.asin(d14 / 2.0) * 1.2740015798544e7


def _distance_of_line(points: list[list[float]]) -> float:
    """计算路径总距离（米）"""
    total = 0.0
    for i in range(len(points) - 1):
        total += _distance_between_points(points[i], points[i + 1])
    return total


def generate_route_for_point_id(
    point_id: str,
    distance_km: float,
    run_date: str | None = None,
    start_time_str: str | None = None,
    duration_min: int | None = None,
) -> dict[str, Any]:
    """
    根据路线 ID 生成跑步轨迹。

    Args:
        point_id: 路线 pointId，如 "sunrunLine-20230208000001"
        distance_km: 目标距离（公里）
        run_date: 跑步日期 YYYY-MM-DD，默认今天
        start_time_str: 开始时间 HH:MM，默认 06:00
        duration_min: 跑步时长（分钟），默认根据距离自动计算

    Returns:
        包含 mockRoute, routeInfo, startTime, endTime 等完整数据
    """
    map_data = _load_map_data()
    run_point_list = map_data.get("runPointList", [])

    # 查找指定路线
    target_route = None
    for rp in run_point_list:
        if rp.get("pointId") == point_id:
            target_route = rp
            break
    if not target_route:
        raise ValueError(f"路线 {point_id} 不存在于 map.json")

    # 解析日期时间
    base_date = datetime.strptime(run_date, "%Y-%m-%d") if run_date else datetime.now()
    if start_time_str:
        h, m = map(int, start_time_str.split(":"))
        start_time = base_date.replace(hour=h, minute=m, second=0)
    else:
        start_time = base_date.replace(hour=6, minute=0, second=0)

    # 计算跑步时长
    if duration_min:
        duration_seconds = duration_min * 60
    else:
        # 正态分布生成 10-25 分钟之间的值
        duration_seconds = int(_normal_random(17.5 * 60, 2.5 * 60))
        duration_seconds = max(10 * 60, min(25 * 60, duration_seconds))

    # ±30秒随机波动
    duration_seconds += int((random.random() - 0.5) * 60)
    end_time = start_time + timedelta(seconds=duration_seconds)

    # 生成轨迹
    std = 1 / 50000
    step_length = 0.0001
    distance_m = distance_km * 1000

    # 路径点转为 [经度, 纬度]
    route = [[float(p["longitude"]), float(p["latitude"])] for p in target_route["pointList"]]

    # 路径点间插值
    def add_points(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = max(math.floor(math.hypot(dx, dy) / step_length), 1)
        return [[a[0] + dx * i / n, a[1] + dy * i / n] for i in range(n)]

    combined = []
    for i in range(len(route) - 1):
        pts = add_points(route[i], route[i + 1])
        combined.extend(pts[:-1])
    combined.append(route[-1])

    # 随机起点 + 偏移
    idx = random.randint(0, len(combined) - 1)

    def add_deviation(p):
        return [_normal_random(p[0], std), _normal_random(p[1], std)]

    points = [add_deviation(combined[idx])]
    current_dist = 0.0
    max_points = min(int(distance_m / 2) + 100, 3000)

    while current_dist < distance_m and len(points) < max_points:
        idx = (idx + 1) % max(1, len(combined) - 1)
        points.append(add_deviation(combined[idx]))
        current_dist = _distance_of_line(points)

    # 格式化输出
    mock_route = [{"longitude": f"{p[0]:.6f}", "latitude": f"{p[1]:.6f}"} for p in points]
    h, rem = divmod(duration_seconds, 3600)
    m, s = divmod(rem, 60)
    used_time = f"{h:02d}:{m:02d}:{s:02d}"
    avg_speed = f"{distance_km / (duration_seconds / 3600):.2f}"

    return {
        "routeInfo": {
            "taskId": target_route["taskId"],
            "pointId": target_route["pointId"],
            "pointName": target_route["pointName"],
        },
        "mockRoute": mock_route,
        "distance": f"{(current_dist / 1000):.2f}",
        "targetDistance": f"{distance_km:.2f}",
        "pointCount": len(mock_route),
        "startTime": start_time.strftime("%H:%M:%S"),
        "endTime": end_time.strftime("%H:%M:%S"),
        "evaluateDate": end_time.strftime("%Y-%m-%d"),
        "usedTime": used_time,
        "durationSeconds": duration_seconds,
        "avgSpeed": avg_speed,
        "steps": f"{1000 + random.randint(0, 1000)}",
    }


def save_route(route_data: dict[str, Any], filename: str) -> str:
    """保存轨迹到 JSON 文件"""
    data_dir = Path(__file__).parent.parent / "data" / "routes"
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / filename
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(route_data, f, ensure_ascii=False, indent=2)
    return str(file_path)


def load_route(filename: str) -> dict[str, Any]:
    """从 JSON 文件加载轨迹"""
    data_dir = Path(__file__).parent.parent / "data" / "routes"
    file_path = data_dir / filename
    if not file_path.exists():
        raise FileNotFoundError(f"轨迹文件不存在: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_route_filename(run_date: str, point_id: str) -> str:
    """生成轨迹文件名"""
    return f"{run_date}_{point_id}.json"
