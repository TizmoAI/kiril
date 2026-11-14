"""
Brand template routes.

A brand template saves a caption style (template, font, size, colour) plus
logo and intro/outro references. ``POST /tasks/`` accepts ``brand_template_id``
and uses the template for every caption setting the request leaves out.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...repositories.brand_template_repository import BrandTemplateRepository
from .tasks import _normalize_font_color, _normalize_font_family, _normalize_font_size, _read_json_object

router = APIRouter(prefix="/brand-templates", tags=["brand-templates"])

MAX_TEMPLATES_PER_USER = 20
MAX_REF_CHARS = 2048


def _text(payload: Dict[str, Any], field: str, limit: int) -> Any:
    value = payload[field]
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(status_code=400, detail=f"{field} must be text of at most {limit} characters")
    return value.strip() or None


def _clean(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the fields present in ``payload``; absent ones are left alone."""
    values: Dict[str, Any] = {}
    if "name" in payload:
        name = _text(payload, "name", 120)
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        values["name"] = name
    if "caption_template" in payload:
        values["caption_template"] = _text(payload, "caption_template", 100)
    for field in ("logo_ref", "intro_ref", "outro_ref"):
        if field in payload:
            values[field] = _text(payload, field, MAX_REF_CHARS)
    if "font_family" in payload:
        values["font_family"] = _normalize_font_family(payload["font_family"])
    if "font_size" in payload:
        values["font_size"] = _normalize_font_size(payload["font_size"])
    if "font_color" in payload:
        values["font_color"] = _normalize_font_color(payload["font_color"])
    return values


async def _user_id(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


@router.get("/")
async def list_brand_templates(request: Request, db: AsyncSession = Depends(get_db)):
    templates = await BrandTemplateRepository.list_for_user(db, await _user_id(request, db))
    return {"brand_templates": templates, "total": len(templates)}


@router.post("/", status_code=201)
async def create_brand_template(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    payload = await _read_json_object(request)
    if "name" not in payload:
        raise HTTPException(status_code=400, detail="name is required")
    values = _clean(payload)
    if await BrandTemplateRepository.count_for_user(db, user_id) >= MAX_TEMPLATES_PER_USER:
        raise HTTPException(status_code=409, detail=f"You can save up to {MAX_TEMPLATES_PER_USER} brand templates")
    return await BrandTemplateRepository.create(db, user_id, values)


@router.get("/{template_id}")
async def get_brand_template(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    template = await BrandTemplateRepository.get(db, await _user_id(request, db), template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Brand template not found")
    return template


@router.patch("/{template_id}")
async def update_brand_template(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    values = _clean(await _read_json_object(request))
    template = await BrandTemplateRepository.update(db, user_id, template_id, values)
    if not template:
        raise HTTPException(status_code=404, detail="Brand template not found")
    return template


@router.delete("/{template_id}", status_code=204)
async def delete_brand_template(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    if not await BrandTemplateRepository.delete(db, await _user_id(request, db), template_id):
        raise HTTPException(status_code=404, detail="Brand template not found")
