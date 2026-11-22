"""FastAPI application and the local inspection interface."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from inspection_trace.api import health, imports, lots, records, scenarios, spc, templates
from inspection_trace.services.errors import QueryError

app = FastAPI(title="Inspection Trace")
app.include_router(health.router)
app.include_router(imports.router)
app.include_router(records.router)
app.include_router(lots.router)
app.include_router(spc.router)
app.include_router(scenarios.router)
app.include_router(templates.router)
app.mount(
    "/ui", StaticFiles(directory=Path(__file__).parent / "web" / "static", html=True), name="ui"
)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.exception_handler(QueryError)
async def query_error(_request: Request, exc: QueryError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
