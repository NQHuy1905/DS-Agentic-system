from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import eda, feature_engineering, hyperparam_opt, model_monitoring, pipeline_orchestration

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(eda.router, prefix="/api/v1")
app.include_router(feature_engineering.router, prefix="/api/v1")
app.include_router(hyperparam_opt.router, prefix="/api/v1")
app.include_router(model_monitoring.router, prefix="/api/v1")
app.include_router(pipeline_orchestration.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
