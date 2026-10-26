"""Settings → Team and the sidebar's channel categories.

WHO MAY DO WHAT
    owner   invites admins and members, changes anyone's role, switches any
            admin's or member's features, turns anyone's access off.
    admin   invites members, switches members' features, turns members off.
            Never touches the owner, another admin, or themself.
    member  nothing here.
    operator (break-glass) nothing here: it looks, it does not grant.

The owner's own features are always on and the owner cannot be turned off,
so the account can never lock itself out (accounts.set_role keeps the last
owner too).
"""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import accounts, auth
from . import threads as threads_mod

router = APIRouter(prefix="/api/team")
categories_router = APIRouter(prefix="/api/categories")


def _manager(sess: dict) -> dict:
    auth.require_person(sess)
    if auth.role_of(sess) not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only an admin or the owner can manage the team.")
    return sess


def _may_manage(sess: dict, target: dict) -> None:
    """Owner manages everyone but the owner role's own guards; an admin
    manages members only."""
    me = auth.person_of(sess)
    if target["person_key"] == me:
        raise HTTPException(status_code=403, detail="You can't change your own access.")
    if target["role"] == "owner":
        raise HTTPException(status_code=403, detail="Nobody can change the owner's access.")
    if auth.role_of(sess) != "owner" and target["role"] != "member":
        raise HTTPException(status_code=403, detail="Only the owner can change an admin's access.")


class InviteBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(..., min_length=3, max_length=320)
    role: str = Field("member", pattern="^(admin|member)$")
    title: str = Field("", max_length=80)


class PersonPatch(BaseModel):
    role: Optional[str] = Field(None, pattern="^(admin|member)$")
    disabled: Optional[bool] = None
    features: Optional[Dict[str, bool]] = None


@router.get("")
def get_team(sess: dict = Depends(auth.current_session)):
    _manager(sess)
    return {"people": accounts.team(), "features": list(accounts.FEATURES),
            "feature_labels": accounts.FEATURE_LABELS,
            "can_invite_admins": auth.role_of(sess) == "owner",
            "invites_ready": accounts.team_file() is not None}


@router.post("/invite", status_code=201)
def post_invite(body: InviteBody, sess: dict = Depends(auth.current_session)):
    _manager(sess)
    if body.role == "admin" and auth.role_of(sess) != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can invite an admin.")
    try:
        acct = accounts.invite(body.name, body.email, body.role, by=auth.person_of(sess), title=body.title)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "person": next(p for p in accounts.team() if p["person_key"] == acct["person_key"])}


@router.patch("/{person_key}")
def patch_person(person_key: str, body: PersonPatch, sess: dict = Depends(auth.current_session)):
    _manager(sess)
    target = accounts.get(person_key)
    if target is None:
        raise HTTPException(status_code=404, detail="No such person on the team.")
    _may_manage(sess, target)
    if body.role is not None and auth.role_of(sess) != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can change someone's role.")
    unknown = set(body.features or {}) - set(accounts.FEATURES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown feature: {', '.join(sorted(unknown))}.")
    by = auth.person_of(sess)
    try:
        if body.role is not None:
            accounts.set_role(person_key, body.role)
        for feature, on in (body.features or {}).items():
            accounts.set_feature(person_key, feature, on, by=by)
        if body.disabled is not None:
            accounts.set_disabled(person_key, body.disabled)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "person": next(p for p in accounts.team() if p["person_key"] == person_key)}


# ── categories ───────────────────────────────────────────────────────────────

class CategoryBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class CategoryPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    position: Optional[int] = Field(None, ge=0, le=1000)


@categories_router.get("")
def get_categories(sess: dict = Depends(auth.current_session)):
    """Every section, for anyone signed in. A section's name is structure; the
    channels inside it are still only the ones this person was added to."""
    return {"categories": threads_mod.categories()}


@categories_router.post("", status_code=201)
def post_category(body: CategoryBody, sess: dict = Depends(auth.current_session)):
    _manager(sess)
    try:
        return {"ok": True, "category": threads_mod.create_category(body.name, auth.person_of(sess))}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@categories_router.patch("/{category_id}")
def patch_category(category_id: str, body: CategoryPatch, sess: dict = Depends(auth.current_session)):
    _manager(sess)
    try:
        return {"ok": True, "category": threads_mod.update_category(category_id, body.name, body.position)}
    except threads_mod.CategoryNotFound:
        raise HTTPException(status_code=404, detail="No such category.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@categories_router.delete("/{category_id}")
def delete_category(category_id: str, sess: dict = Depends(auth.current_session)):
    _manager(sess)
    try:
        moved = threads_mod.delete_category(category_id)
    except threads_mod.CategoryNotFound:
        raise HTTPException(status_code=404, detail="No such category.")
    return {"ok": True, "channels_ungrouped": moved}
