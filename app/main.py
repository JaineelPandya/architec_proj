from fastapi import FastAPI
from app.api.routes import router as projects_router
from app.api.intelligence import router as intelligence_router

app = FastAPI(
    title="Architecture Project Generator",
    description=(
        "Upload an existing codebase and generate "
        "a structured project analysis."
    ),
    version="0.1.0",
)

app.include_router(projects_router)
app.include_router(intelligence_router)


@app.get("/")
def health_check():
    return {
        "status": "running",
        "service": "Architecture Project Generator",
    }