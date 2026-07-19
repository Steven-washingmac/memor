"""SQLite 数据库 - 支持多用户 session 存储"""

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.config import DATABASE_FILE


def _get_db_path() -> str:
    Path(DATABASE_FILE).parent.mkdir(parents=True, exist_ok=True)
    return DATABASE_FILE


def init_db() -> None:
    """初始化数据库表"""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                openid TEXT UNIQUE,
                stu_number TEXT,
                stu_name TEXT,
                school_id TEXT,
                school_name TEXT,
                campus_id TEXT,
                campus_name TEXT,
                college_name TEXT,
                phone_number TEXT,
                token TEXT,
                server_path TEXT DEFAULT 'https://app.xtotoro.com',
                session_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stu_number TEXT NOT NULL,
                run_date TEXT NOT NULL,
                route_id TEXT,
                distance TEXT,
                used_time TEXT,
                avg_speed TEXT,
                scantron_id TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def save_user(session_data: dict[str, Any]) -> None:
    """保存或更新用户 session"""
    s = session_data
    with sqlite3.connect(_get_db_path()) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE stu_number = ?", (s.get("stuNumber"),)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE users SET
                    stu_name = ?, school_id = ?, school_name = ?,
                    campus_id = ?, campus_name = ?, college_name = ?,
                    phone_number = ?, token = ?, server_path = ?,
                    session_data = ?, updated_at = CURRENT_TIMESTAMP
                WHERE stu_number = ?
            """, (
                s.get("stuName"), s.get("schoolId"), s.get("schoolName"),
                s.get("campusId"), s.get("campusName"), s.get("collegeName"),
                s.get("phoneNumber"), s.get("token"), s.get("serverPath", "https://app.xtotoro.com"),
                json.dumps(s, ensure_ascii=False), s.get("stuNumber"),
            ))
        else:
            conn.execute("""
                INSERT INTO users (stu_number, stu_name, school_id, school_name,
                    campus_id, campus_name, college_name, phone_number, token,
                    server_path, session_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s.get("stuNumber"), s.get("stuName"), s.get("schoolId"), s.get("schoolName"),
                s.get("campusId"), s.get("campusName"), s.get("collegeName"),
                s.get("phoneNumber"), s.get("token"), s.get("serverPath", "https://app.xtotoro.com"),
                json.dumps(s, ensure_ascii=False),
            ))
        conn.commit()


def get_user(stu_number: str) -> dict[str, Any] | None:
    """根据学号获取用户 session"""
    with sqlite3.connect(_get_db_path()) as conn:
        row = conn.execute(
            "SELECT session_data FROM users WHERE stu_number = ?", (stu_number,)
        ).fetchone()
        if row:
            return json.loads(row[0])
    return None


def get_all_users() -> list[dict[str, Any]]:
    """获取所有用户"""
    with sqlite3.connect(_get_db_path()) as conn:
        rows = conn.execute("SELECT session_data FROM users ORDER BY updated_at DESC").fetchall()
        return [json.loads(row[0]) for row in rows]


def get_current_user() -> dict[str, Any] | None:
    """获取最近登录的用户（当前活跃用户）"""
    with sqlite3.connect(_get_db_path()) as conn:
        row = conn.execute(
            "SELECT session_data FROM users ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return json.loads(row[0])
    return None


def delete_user(stu_number: str) -> None:
    """删除用户"""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("DELETE FROM users WHERE stu_number = ?", (stu_number,))
        conn.commit()


def save_run_task(stu_number: str, run_date: str, route_id: str,
                  distance: str, used_time: str, avg_speed: str,
                  scantron_id: str = "", status: str = "success") -> None:
    """保存跑步任务记录"""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("""
            INSERT INTO run_tasks (stu_number, run_date, route_id, distance,
                used_time, avg_speed, scantron_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (stu_number, run_date, route_id, distance, used_time, avg_speed,
              scantron_id, status))
        conn.commit()


def get_run_tasks(stu_number: str, limit: int = 30) -> list[dict[str, Any]]:
    """获取跑步任务历史"""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM run_tasks WHERE stu_number = ? ORDER BY created_at DESC LIMIT ?",
            (stu_number, limit)
        ).fetchall()
        return [dict(row) for row in rows]


# 启动时初始化数据库
init_db()
