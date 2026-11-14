"""
Brand template repository - saved caption style, logo and intro/outro references.
"""

from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FIELDS = (
    "name",
    "caption_template",
    "font_family",
    "font_size",
    "font_color",
    "logo_ref",
    "intro_ref",
    "outro_ref",
)
COLUMNS = "id, " + ", ".join(FIELDS) + ", created_at, updated_at"


class BrandTemplateRepository:
    @staticmethod
    async def list_for_user(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(f"SELECT {COLUMNS} FROM brand_templates WHERE user_id = :user_id ORDER BY created_at DESC"),
            {"user_id": user_id},
        )
        return [dict(row) for row in result.mappings().all()]

    @staticmethod
    async def get(db: AsyncSession, user_id: str, template_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(f"SELECT {COLUMNS} FROM brand_templates WHERE id = :id AND user_id = :user_id"),
            {"id": template_id, "user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    async def count_for_user(db: AsyncSession, user_id: str) -> int:
        result = await db.execute(
            text("SELECT COUNT(*) FROM brand_templates WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        return result.scalar_one()

    @staticmethod
    async def create(db: AsyncSession, user_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        result = await db.execute(
            text(
                f"""
                INSERT INTO brand_templates (id, user_id, {", ".join(FIELDS)})
                VALUES (:id, :user_id, {", ".join(":" + f for f in FIELDS)})
                RETURNING {COLUMNS}
                """
            ),
            {"id": str(uuid4()), "user_id": user_id, **{f: values.get(f) for f in FIELDS}},
        )
        row = result.mappings().first()
        await db.commit()
        return dict(row)

    @staticmethod
    async def update(
        db: AsyncSession, user_id: str, template_id: str, values: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Set only the given fields; a ``None`` value clears an optional one."""
        if not values:
            return await BrandTemplateRepository.get(db, user_id, template_id)
        assignments = ", ".join(f"{field} = :{field}" for field in values)
        result = await db.execute(
            text(
                f"""
                UPDATE brand_templates SET {assignments}, updated_at = NOW()
                WHERE id = :id AND user_id = :user_id
                RETURNING {COLUMNS}
                """
            ),
            {"id": template_id, "user_id": user_id, **values},
        )
        row = result.mappings().first()
        await db.commit()
        return dict(row) if row else None

    @staticmethod
    async def delete(db: AsyncSession, user_id: str, template_id: str) -> bool:
        result = await db.execute(
            text("DELETE FROM brand_templates WHERE id = :id AND user_id = :user_id"),
            {"id": template_id, "user_id": user_id},
        )
        await db.commit()
        return result.rowcount > 0
