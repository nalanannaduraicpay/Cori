"""FastAPI application: API + demo UI.

    uvicorn cori.api.main:app --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cori.api.routes import router
from cori.db.connection import close_pools
from cori.db.repository import recover_interrupted_batches

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:  # batches run in-process; any left RUNNING by a previous server stop are released
        stuck = recover_interrupted_batches()
        if stuck:
            logging.getLogger(__name__).warning("marked %d interrupted batch(es): %s", len(stuck), stuck)
    except Exception:
        logging.getLogger(__name__).exception("could not check for interrupted batches (database down?)")
    yield
    close_pools()


app = FastAPI(
    lifespan=lifespan,
    title="Cori Invoice Review API",
    version="1.0.0",
    description="Multi-agent first-pass invoice review for IAS. Cori advances invoices to the approval "
                "section or routes them to human review. Cori never approves payment.",
)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
