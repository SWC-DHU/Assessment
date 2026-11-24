from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, model_assets, resource_matching, samples, semantics, sessions, templates
from app.config import settings
from domain_semantics.catalog import check_ontology
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_initial_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    check_ontology()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_initial_data(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Process Agent Playground API",
    version="0.1.0",
    description="轻量级工艺需求解析智能体测试与模板填充平台",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(templates.router)
app.include_router(sessions.router)
app.include_router(samples.router)
app.include_router(semantics.router)
app.include_router(model_assets.router)
app.include_router(resource_matching.router)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=True,
    )
