"""Brand templates: CRUD, ownership, and use when a task is created."""

import hashlib
import hmac
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.fixtures.factories import create_user

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
STYLE = {
    "name": "Acme",
    "caption_template": "bold",
    "font_family": "Montserrat",
    "font_size": 40,
    "font_color": "#ffcc00",
    "logo_ref": "upload://logo.png",
    "intro_ref": "upload://intro.mp4",
}


def headers_for(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-kiril-user-id": user_id, "x-kiril-ts": timestamp, "x-kiril-signature": signature}


@pytest.fixture
async def owner(db_session):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    return user_id


@pytest.fixture(autouse=True)
def youtube_preflight(monkeypatch):
    async def info(_url, **_kwargs):
        return {"duration": 600, "title": "Brand source"}

    monkeypatch.setattr("src.api.routes.tasks.async_get_youtube_video_info", info)


async def create_template(client, user_id, **overrides):
    response = await client.post("/brand-templates/", headers=headers_for(user_id), json={**STYLE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def task_style(db_session, task_id):
    row = (
        await db_session.execute(
            text("SELECT font_family, font_size, font_color, caption_template FROM tasks WHERE id = :id"),
            {"id": task_id},
        )
    ).one()
    return tuple(row)


@pytest.mark.asyncio
async def test_crud_roundtrip(client, owner):
    headers = headers_for(owner)
    created = await create_template(client, owner)
    assert created["font_color"] == "#FFCC00"
    assert created["logo_ref"] == "upload://logo.png"

    listed = (await client.get("/brand-templates/", headers=headers)).json()
    assert [t["id"] for t in listed["brand_templates"]] == [created["id"]]

    patched = await client.patch(
        f"/brand-templates/{created['id']}", headers=headers, json={"name": "Acme 2", "intro_ref": None}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Acme 2"
    assert patched.json()["intro_ref"] is None
    assert patched.json()["font_family"] == "Montserrat"

    assert (await client.delete(f"/brand-templates/{created['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/brand-templates/{created['id']}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_templates_are_private_to_their_owner(client, db_session, owner):
    other = str(uuid4())
    await create_user(db_session, user_id=other)
    created = await create_template(client, owner)

    assert (await client.get(f"/brand-templates/{created['id']}", headers=headers_for(other))).status_code == 404
    assert (await client.patch(f"/brand-templates/{created['id']}", headers=headers_for(other), json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/brand-templates/{created['id']}", headers=headers_for(other))).status_code == 404
    assert (await client.get("/brand-templates/", headers=headers_for(other))).json()["total"] == 0


@pytest.mark.asyncio
async def test_rejects_bad_input(client, owner):
    headers = headers_for(owner)
    assert (await client.post("/brand-templates/", headers=headers, json={})).status_code == 400
    assert (await client.post("/brand-templates/", headers=headers, json={"name": "  "})).status_code == 400
    assert (await client.post("/brand-templates/", headers=headers, json={"name": "x", "logo_ref": 5})).status_code == 400
    assert (await client.get("/brand-templates/")).status_code in {401, 403}


@pytest.mark.asyncio
async def test_task_uses_template_for_unset_caption_settings(client, db_session, owner):
    created = await create_template(client, owner)

    response = await client.post(
        "/tasks/",
        headers=headers_for(owner),
        json={"source": {"url": YOUTUBE_URL}, "brand_template_id": created["id"]},
    )

    assert response.status_code == 200, response.text
    assert await task_style(db_session, response.json()["task_id"]) == ("Montserrat", 40, "#FFCC00", "bold")


@pytest.mark.asyncio
async def test_request_values_win_over_the_template(client, db_session, owner):
    created = await create_template(client, owner)

    response = await client.post(
        "/tasks/",
        headers=headers_for(owner),
        json={
            "source": {"url": YOUTUBE_URL},
            "brand_template_id": created["id"],
            "font_options": {"font_size": 20},
            "caption_template": "default",
        },
    )

    assert response.status_code == 200, response.text
    assert await task_style(db_session, response.json()["task_id"]) == ("Montserrat", 20, "#FFCC00", "default")


@pytest.mark.asyncio
async def test_unknown_or_foreign_template_is_refused_before_any_task(client, db_session, owner):
    other = str(uuid4())
    await create_user(db_session, user_id=other)
    foreign = await create_template(client, other)

    for template_id in (foreign["id"], "missing", 7):
        response = await client.post(
            "/tasks/",
            headers=headers_for(owner),
            json={"source": {"url": YOUTUBE_URL}, "brand_template_id": template_id},
        )
        assert response.status_code == 404, response.text
    count = (await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = :u"), {"u": owner})).scalar()
    assert count == 0
