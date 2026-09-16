"""FastAPI entrypoint.  Run:  python app.py   (or: uvicorn app:app --reload)"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from router import router

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Resizes any logo (square or horizontal) to "
        f"{settings.TARGET_SIZE}x{settings.TARGET_SIZE} px without changing the artwork."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", tags=["meta"])
def root():
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "endpoints": [
            f"{settings.API_PREFIX}/logo/health",
            f"{settings.API_PREFIX}/logo/resize",
            f"{settings.API_PREFIX}/logo/batch",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=True)
