"""Droits par espace : une équipe rattachée à un espace avec un profil donne à
ses membres des capacités supplémentaires sur cet espace seulement (union)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities
from tests.conftest import bootstrap_admin, create_plain_user, login


async def _profile(
    app: FastAPI, name: str, caps: list[str], *, is_admin: bool = False
) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        value = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities, is_admin) VALUES ($1, $2, $3) RETURNING id",
            name,
            caps,
            is_admin,
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _space(app: FastAPI, name: str) -> uuid.UUID:
    async with app.state.pool.acquire() as conn:
        connection_id = await conn.fetchval("SELECT id FROM connections LIMIT 1")
        if connection_id is None:
            await conn.execute(
                "INSERT INTO secrets (slug, label, category, value_encrypted) "
                "VALUES ('gh-droits', 'x', 'github', '\\x00')"
            )
            connection_id = await conn.fetchval(
                "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, "
                "last_status) VALUES ('GH', 'gh', 'github', 'https://github.com', 'gh-droits', "
                "'moi', 'valid') RETURNING id"
            )
        value = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ($1, $2, $3, 'valid') RETURNING id",
            name,
            connection_id,
            f"https://github.com/o/{name}",
        )
    assert isinstance(value, uuid.UUID)
    return value


async def _user_in_team(
    app: FastAPI, email: str, team: str, *, base_profile: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    await create_plain_user(app, email, "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "UPDATE users SET profile_id = $2 WHERE email = $1 RETURNING id", email, base_profile
        )
        team_id = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ($1, $1, 'manual') RETURNING id", team
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            user_id,
            team_id,
        )
    return user_id, team_id


async def _attach(
    app: FastAPI, space_id: uuid.UUID, team_id: uuid.UUID, profile_id: uuid.UUID
) -> None:
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO space_team_profiles (space_id, team_id, profile_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (space_id, team_id) DO UPDATE SET profile_id = EXCLUDED.profile_id",
            space_id,
            team_id,
            profile_id,
        )


async def _caps(app: FastAPI, user_id: uuid.UUID, space_id: uuid.UUID | None) -> set[str]:
    async with app.state.pool.acquire() as conn:
        user = User.from_row(await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id))
        return await load_capabilities(conn, user, space_id)


@asynccontextmanager
async def _as(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, email, "motdepasse-solide")
        yield client


# --- Calcul des capacités ----------------------------------------------------


async def test_equipe_rattachee_donne_publish_sur_cet_espace_seulement(app: FastAPI) -> None:
    contrib = await _profile(app, "Contributeur", ["spaces:read", "spaces:publish"])
    e1, e2 = await _space(app, "e1"), await _space(app, "e2")
    alice, data = await _user_in_team(app, "alice@x.org", "data")
    await _attach(app, e1, data, contrib)
    assert "spaces:publish" in await _caps(app, alice, e1)
    assert "spaces:publish" not in await _caps(app, alice, e2)
    assert "spaces:publish" not in await _caps(app, alice, None)  # hors espace : inchangé
    async with _as(app, "alice@x.org") as client:
        allowed = await client.post(f"/api/spaces/{e1}/branches/{uuid.uuid4()}/publish")
        refused = await client.post(f"/api/spaces/{e2}/branches/{uuid.uuid4()}/publish")
    assert allowed.status_code != 403  # passe la garde (puis branche introuvable)
    assert refused.status_code == 403


async def test_profils_differents_par_espace(app: FastAPI) -> None:
    lecteur = await _profile(app, "Lecteur", ["spaces:read", "sources:read"])
    contrib = await _profile(app, "Contrib", ["spaces:read", "sources:declare"])
    e1, e2 = await _space(app, "e1"), await _space(app, "e2")
    bob, data = await _user_in_team(app, "bob@x.org", "data")
    await _attach(app, e1, data, lecteur)
    await _attach(app, e2, data, contrib)
    assert {"sources:read"} <= await _caps(app, bob, e1)
    assert "sources:declare" not in await _caps(app, bob, e1)
    assert "sources:declare" in await _caps(app, bob, e2)


async def test_union_avec_le_profil_de_base(app: FastAPI) -> None:
    base = await _profile(app, "Base", ["sources:read"])
    contrib = await _profile(app, "Contrib", ["sources:declare"])
    e1 = await _space(app, "e1")
    carol, data = await _user_in_team(app, "carol@x.org", "data", base_profile=base)
    await _attach(app, e1, data, contrib)
    assert {"sources:read", "sources:declare"} <= await _caps(app, carol, e1)


async def test_liste_des_espaces_filtree_sans_spaces_read_de_base(app: FastAPI) -> None:
    lecteur = await _profile(app, "Lecteur", ["spaces:read"])
    e1, _e2 = await _space(app, "e1"), await _space(app, "e2")
    _dave, data = await _user_in_team(app, "dave@x.org", "data")
    await _attach(app, e1, data, lecteur)
    async with _as(app, "dave@x.org") as client:
        response = await client.get("/api/spaces")
    assert response.status_code == 200
    assert [s["id"] for s in response.json()["spaces"]] == [str(e1)]


async def test_liste_complete_avec_spaces_read_de_base(app: FastAPI) -> None:
    base = await _profile(app, "Base", ["spaces:read"])
    await _space(app, "e1")
    await _space(app, "e2")
    await _user_in_team(app, "erin@x.org", "data", base_profile=base)
    async with _as(app, "erin@x.org") as client:
        response = await client.get("/api/spaces")
    assert len(response.json()["spaces"]) == 2


async def test_sans_aucun_droit_liste_vide(app: FastAPI) -> None:
    await _space(app, "e1")
    await _user_in_team(app, "fred@x.org", "data")
    async with _as(app, "fred@x.org") as client:
        response = await client.get("/api/spaces")
    assert response.status_code == 200
    assert response.json()["spaces"] == []


async def test_rattachement_admin_ne_donne_aucune_capacite_globale(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        admin = await conn.fetchval("SELECT id FROM profiles WHERE is_admin")
    e1 = await _space(app, "e1")
    gina, data = await _user_in_team(app, "gina@x.org", "data")
    await _attach(app, e1, data, admin)
    caps = await _caps(app, gina, e1)
    assert {"spaces:publish", "governance:write", "curation:rules"} <= caps
    assert "secrets:read" not in caps
    assert "spaces:create" not in caps
    async with _as(app, "gina@x.org") as client:
        assert (await client.get("/api/secrets")).status_code == 403


async def test_capacite_globale_du_profil_rattache_ignoree(app: FastAPI) -> None:
    mixte = await _profile(app, "Mixte", ["spaces:read", "secrets:read", "users:write"])
    e1 = await _space(app, "e1")
    hugo, data = await _user_in_team(app, "hugo@x.org", "data")
    await _attach(app, e1, data, mixte)
    caps = await _caps(app, hugo, e1)
    assert "spaces:read" in caps
    assert "secrets:read" not in caps
    assert "users:write" not in caps


async def test_retrait_effectif_a_la_requete_suivante(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    contrib = await _profile(app, "Contrib", ["spaces:read", "spaces:publish"])
    e1 = await _space(app, "e1")
    _ines, data = await _user_in_team(app, "ines@x.org", "data")
    put = await client.put(f"/api/spaces/{e1}/teams/{data}", json={"profile_id": str(contrib)})
    assert put.status_code == 200, put.text
    async with _as(app, "ines@x.org") as user_client:
        url = f"/api/spaces/{e1}/branches/{uuid.uuid4()}/publish"
        assert (await user_client.post(url)).status_code != 403
        assert (await client.delete(f"/api/spaces/{e1}/teams/{data}")).status_code == 204
        assert (await user_client.post(url)).status_code == 403


async def test_routes_de_rattachement(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    lecteur = await _profile(app, "Lecteur", ["spaces:read"])
    e1 = await _space(app, "e1")
    _jo, data = await _user_in_team(app, "jo@x.org", "data")
    assert (
        await client.put(f"/api/spaces/{e1}/teams/{data}", json={"profile_id": str(lecteur)})
    ).status_code == 200
    listing = (await client.get(f"/api/spaces/{e1}/teams")).json()["teams"]
    assert listing == [
        {
            "team_id": str(data),
            "team_name": "data",
            "profile_id": str(lecteur),
            "profile_name": "Lecteur",
        }
    ]
    unknown = await client.put(
        f"/api/spaces/{e1}/teams/{uuid.uuid4()}", json={"profile_id": str(lecteur)}
    )
    assert unknown.status_code == 404


async def test_mes_capacites_sur_un_espace(app: FastAPI) -> None:
    contrib = await _profile(app, "Contrib", ["spaces:read", "sources:declare"])
    e1 = await _space(app, "e1")
    _kim, data = await _user_in_team(app, "kim@x.org", "data")
    await _attach(app, e1, data, contrib)
    async with _as(app, "kim@x.org") as client:
        mine = await client.get(f"/api/spaces/{e1}/me")
        session = await client.get("/api/session")
    assert set(mine.json()["capabilities"]) == {"spaces:read", "sources:declare"}
    # Le menu doit montrer « Espaces » : capacités par espace exposées à part.
    assert "spaces:read" in session.json()["space_capabilities"]
    assert "spaces:read" not in session.json()["capabilities"]


async def test_suppression_equipe_ou_espace_supprime_rattachements(app: FastAPI) -> None:
    lecteur = await _profile(app, "Lecteur", ["spaces:read"])
    e1, e2 = await _space(app, "e1"), await _space(app, "e2")
    _lea, data = await _user_in_team(app, "lea@x.org", "data")
    await _attach(app, e1, data, lecteur)
    await _attach(app, e2, data, lecteur)
    async with app.state.pool.acquire() as conn:
        await conn.execute("DELETE FROM git_spaces WHERE id = $1", e1)
        assert await conn.fetchval("SELECT count(*) FROM space_team_profiles") == 1
        await conn.execute("DELETE FROM teams WHERE id = $1", data)
        assert await conn.fetchval("SELECT count(*) FROM space_team_profiles") == 0


async def test_gerer_rattachements_sans_profiles_write_403(app: FastAPI) -> None:
    lecteur = await _profile(app, "Lecteur", ["spaces:read"])
    e1 = await _space(app, "e1")
    _max, data = await _user_in_team(app, "max@x.org", "data", base_profile=lecteur)
    async with _as(app, "max@x.org") as client:
        response = await client.put(
            f"/api/spaces/{e1}/teams/{data}", json={"profile_id": str(lecteur)}
        )
    assert response.status_code == 403


async def test_espace_inconnu_404_apres_authentification(app: FastAPI) -> None:
    base = await _profile(app, "Base", ["spaces:read"])
    await _user_in_team(app, "ned@x.org", "data", base_profile=base)
    async with _as(app, "ned@x.org") as client:
        response = await client.get(f"/api/spaces/{uuid.uuid4()}/me")
    assert response.status_code == 404


async def test_fiche_utilisateur_montre_les_droits_par_espace(
    client: AsyncClient, app: FastAPI
) -> None:
    await bootstrap_admin(client)
    contrib = await _profile(app, "Contributeur", ["spaces:read"])
    e1 = await _space(app, "e1")
    _olga, data = await _user_in_team(app, "olga@x.org", "data")
    await _attach(app, e1, data, contrib)
    users = (await client.get("/api/users")).json()["users"]
    olga = next(u for u in users if u["email"] == "olga@x.org")
    assert olga["space_rights"] == [
        {
            "space_id": str(e1),
            "space_name": "e1",
            "profile_name": "Contributeur",
            "team_name": "data",
        }
    ]
