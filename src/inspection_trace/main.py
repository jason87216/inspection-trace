"""FastAPI application and the local inspection interface."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from inspection_trace.api import health, imports, lots, records

app = FastAPI(title="Inspection Trace")
app.include_router(health.router)
app.include_router(imports.router)
app.include_router(records.router)
app.include_router(lots.router)
app.mount(
    "/ui", StaticFiles(directory=Path(__file__).parent / "web" / "static", html=True), name="ui"
)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/ui/")
