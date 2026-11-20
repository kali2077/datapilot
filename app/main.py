"""DataPilot 平台入口：FastAPI 应用工厂。"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .db import Base, engine, SessionLocal
from . import models  # noqa: F401  确保模型全部注册


def create_app() -> FastAPI:
    app = FastAPI(title="DataPilot", version=config.VERSION,
                  description="AI-Native 大数据平台（引擎开源可插拔 + 平台全自研）")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"])

    from .routers import (auth, datasources, meta, integ, sched, chat, asset,
                          apigw, audit, query, monitor, hadoop, tools, airflow)
    app.include_router(auth.router)
    app.include_router(datasources.router)
    app.include_router(meta.router)
    app.include_router(integ.router)
    app.include_router(sched.router)
    app.include_router(airflow.router)
    app.include_router(chat.router)
    app.include_router(asset.router)
    app.include_router(apigw.router)
    app.include_router(audit.router)
    app.include_router(query.router)
    app.include_router(monitor.router)
    app.include_router(hadoop.router)
    app.include_router(tools.router)
    app.include_router(apigw.public_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": config.VERSION,
                "scheduler": config.SCHEDULER_ENGINE,
                "llm": config.AI_MODEL if config.AI_API_KEY else "rule-engine"}

    @app.on_event("startup")
    def startup():
        Base.metadata.create_all(engine)
        from .db import run_migrations
        run_migrations()
        from . import seed
        seed.run()
        # 调度基建：等待轮询器 + 监控规则在任意引擎模式下都由平台自身执行
        # （rebuild_jobs 内部自行判断：仅 local 注册本地 cron 与元数据采集）
        from .services import sched_service
        db = SessionLocal()
        try:
            sched_service.rebuild_jobs(db)
        finally:
            db.close()
        vec_ok = False
        try:
            from .services.embedder import get_embedder
            vec_ok = get_embedder() is not None
        except Exception:
            pass
        print(f"[DataPilot] v{config.VERSION} 启动完成 | 调度引擎: {config.SCHEDULER_ENGINE} "
              f"| LLM: {'配置' if config.AI_API_KEY else '规则引擎(离线)'}"
              f"| 向量检索: {'启用' if vec_ok else '词元匹配(降级)'}")

    # 前端构建产物
    web_dist = os.path.join(config.BASE_DIR, "web", "dist")
    if os.path.isdir(web_dist):
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


app = create_app()
