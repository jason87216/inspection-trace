"""Local scenario presets and isolated dataset generation."""

import json
import os
import tomllib
from pathlib import Path
from threading import Lock, RLock
from typing import Annotated, Literal
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException
from fastapi import Path as RoutePath
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from generator.config import build_config, load_config
from generator.pipeline import run
from inspection_trace.scenario_schema import Preset, Preview, builtin, parse_configuration, preview

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
BUILTIN = "builtin-abf"
STORAGE_LOCK = RLock()
GENERATION_LOCK = Lock()
type PresetId = Annotated[str, RoutePath(pattern=r"^(builtin-abf|[0-9a-f]{32})$")]
type RunId = Annotated[str, RoutePath(pattern=r"^[0-9a-f]{32}$")]
type Scale = Literal["small", "medium", "large"]


class Entry(BaseModel):
    id: str
    revision: int
    preset: Preset


class Listing(BaseModel):
    id: str
    name: str


class Update(BaseModel):
    revision: Annotated[int, Field(ge=0)]
    preset: Preset


class PreviewRequest(BaseModel):
    preset: Preset
    scale: Scale = "small"


class ImportRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=50)] = "匯入情境"
    text: Annotated[str, Field(min_length=1, max_length=200_000)]
    dirty_toml: Annotated[str, Field(max_length=50_000)] | None = None


class GenerateRequest(BaseModel):
    revision: Annotated[int, Field(ge=0)]
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)]
    scale: Scale = "small"
    confirm: Literal[True]


class Generated(BaseModel):
    id: str
    download_url: str
    summary: dict[str, object]


def _root() -> Path:
    return Path(os.environ.get("INSPECTION_SCENARIO_ROOT", "out/scenarios"))


def _file(preset_id: str) -> Path:
    return _root() / "presets" / f"{preset_id}.json"


def _read(preset_id: str) -> Entry:
    if preset_id == BUILTIN:
        return Entry(id=BUILTIN, revision=0, preset=builtin())
    try:
        return Entry.model_validate_json(_file(preset_id).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(404, "找不到此情境") from exc


def _write(entry: Entry) -> Entry:
    path = _file(entry.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as output:
        output.write(entry.model_dump_json(indent=2))
    temporary.replace(path)
    return entry


def _require_current(preset_id: str, revision: int) -> Entry:
    current = _read(preset_id)
    if current.revision != revision:
        raise HTTPException(409, "情境已變更，請重新開啟後再操作")
    return current


def _check(preset: Preset, scale: Scale = "small") -> None:
    report = preview(preset, scale)
    if report.errors:
        raise HTTPException(422, report.errors)


@router.get("")
def list_presets() -> list[Listing]:
    with STORAGE_LOCK:
        presets = [
            _read(BUILTIN),
            *(_read(path.stem) for path in sorted((_root() / "presets").glob("*.json"))),
        ]
    return [Listing(id=entry.id, name=entry.preset.name) for entry in presets]


@router.post("/validate")
def validate_preset(payload: PreviewRequest) -> Preview:
    return preview(payload.preset, payload.scale)


@router.post("/import")
def import_configuration(payload: ImportRequest) -> Preset:
    try:
        if payload.text.lstrip().startswith("{"):
            data = json.loads(payload.text)
            return parse_configuration(
                data.get("name", payload.name), data["config"], data["dirty"]
            )
        dirty = (
            tomllib.loads(payload.dirty_toml)
            if payload.dirty_toml
            else builtin().dirty.model_dump()
        )
        return parse_configuration(payload.name, tomllib.loads(payload.text), dirty)
    except ValidationError as exc:
        detail = [
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        ]
        raise HTTPException(422, detail) from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(422, "設定檔格式錯誤，請選擇 P11 JSON 或情境 TOML") from exc


@router.get("/runs/{run_id}/download")
def download_dataset(run_id: RunId) -> FileResponse:
    archive = _root() / "runs" / run_id / "dataset.zip"
    if not archive.is_file():
        raise HTTPException(404, "找不到已完成的資料集")
    return FileResponse(archive, media_type="application/zip", filename=f"inspection-{run_id}.zip")


@router.get("/{preset_id}")
def get_preset(preset_id: PresetId) -> Entry:
    return _read(preset_id)


@router.post("", status_code=201)
def create_preset(preset: Preset) -> Entry:
    _check(preset)
    with STORAGE_LOCK:
        return _write(Entry(id=uuid4().hex, revision=1, preset=preset))


@router.put("/{preset_id}")
def update_preset(preset_id: PresetId, payload: Update) -> Entry:
    if preset_id == BUILTIN:
        raise HTTPException(409, "內建 ABF 情境需複製後編輯")
    _check(payload.preset)
    with STORAGE_LOCK:
        _require_current(preset_id, payload.revision)
        return _write(Entry(id=preset_id, revision=payload.revision + 1, preset=payload.preset))


@router.delete("/{preset_id}", status_code=204)
def archive_preset(preset_id: PresetId, revision: int) -> None:
    if preset_id == BUILTIN:
        raise HTTPException(409, "內建 ABF 情境不能刪除")
    with STORAGE_LOCK:
        _require_current(preset_id, revision)
        archive = _root() / "archived"
        archive.mkdir(parents=True, exist_ok=True)
        _file(preset_id).rename(archive / f"{preset_id}.json")


@router.post("/{preset_id}/runs", status_code=201)
def generate_dataset(preset_id: PresetId, payload: GenerateRequest) -> Generated:
    with STORAGE_LOCK:
        entry = _require_current(preset_id, payload.revision)
    _check(entry.preset, payload.scale)
    if not GENERATION_LOCK.acquire(blocking=False):
        raise HTTPException(409, "有一個資料集正在產生，請稍後再試")
    try:
        run_id = uuid4().hex
        folder = _root() / "runs" / run_id
        folder.mkdir(parents=True)
        destination = folder / "dataset"
        preset = entry.preset
        config = (
            load_config()
            if preset_id == BUILTIN
            else build_config(preset.config.model_dump(), preset.dirty.model_dump())
        )
        summary = run(config, payload.scale, payload.seed, destination)
        (destination / "preset.json").write_text(preset.model_dump_json(indent=2), encoding="utf-8")
        temporary = folder / "dataset.zip.tmp"
        with ZipFile(temporary, "x", ZIP_DEFLATED) as archive:
            for path in sorted(destination.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(destination))
        temporary.replace(folder / "dataset.zip")
        return Generated(
            id=run_id,
            download_url=f"/scenarios/runs/{run_id}/download",
            summary=summary,
        )
    finally:
        GENERATION_LOCK.release()
