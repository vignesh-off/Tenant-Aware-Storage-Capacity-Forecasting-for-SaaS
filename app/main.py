from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.routers import tenants, forecasts, admin, audit

app = FastAPI(
    title="Tenant-Aware Storage & Capacity Forecaster API",
    description=(
        "Hierarchical multi-tenant capacity forecaster at tenant, table, and index level. "
        "Provides proactive capacity exhaustion alerts, quantile uncertainty bounds (P10/P50/P90), "
        "role-based access control, and edge-case resilience."
    ),
    version="1.0.0"
)

# Configure CORS for local Streamlit development and container routing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(tenants.router)
app.include_router(forecasts.router)
app.include_router(admin.router)
app.include_router(audit.router)


@app.on_event("startup")
def on_startup():
    """Ensure all database schema tables exist on application startup."""
    init_db()


@app.get("/health", tags=["system"])
def health_check():
    """Health check endpoint for Docker / orchestration liveness."""
    return {"status": "healthy", "service": "tenant-capacity-forecaster-api"}


@app.get("/", tags=["system"])
def root():
    """Root info endpoint."""
    return {
        "message": "Welcome to the Tenant-Aware Storage & Capacity Forecaster API",
        "docs_url": "/docs",
        "health_url": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
