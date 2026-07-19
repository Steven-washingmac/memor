"""
龙猫校园刷跑服务 - FastAPI 入口

启动方式:
    cd campus-run
    pip install -r requirements.txt
    python -m backend.main

访问 http://localhost:8000
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.auth import router as auth_router
from backend.api.history import router as history_router
from backend.api.run import router as run_router
from backend.api.session import router as session_router

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = FastAPI(
    title="龙猫校园刷跑服务",
    description="龙猫校园 Web 客户端 - 微信扫码登录，自动生成轨迹并提交跑步记录",
    version="1.0.0",
)

# CORS 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(auth_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(run_router, prefix="/api")
app.include_router(history_router, prefix="/api")

# 前端静态文件目录（dist 为构建产物，优先级高）
_frontend_dir = Path(__file__).parent.parent / "frontend"
_dist_dir = _frontend_dir / "dist"

# 如果构建产物存在则挂载 assets
if _dist_dir.exists():
    app.mount("/assets", StaticFiles(directory=_dist_dir / "assets"), name="assets")


@app.get("/")
async def root():
    """首页"""
    # 优先用构建产物，否则用 standalone 版本
    if (_dist_dir / "index.html").exists():
        return FileResponse(_dist_dir / "index.html")
    return FileResponse(_frontend_dir / "index.html")


@app.get("/{path:path}")
async def catch_all(path: str):
    """SPA 回退 — 非 API 路径都返回首页"""
    # 阻止递归：排除静态资源请求
    if path.startswith("api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if (_dist_dir / "index.html").exists():
        return FileResponse(_dist_dir / "index.html")
    return FileResponse(_frontend_dir / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
