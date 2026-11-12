"""Jarvis's door to the lead tracker: add a lead, log a contact, list what is
due — as the person in the running chat turn, with the same reach the Leads
screen gives them (a member their own leads, owner and admin everyone's).

Writing the app's own lead store is not an outbound action, so nothing here
goes through Sentinel; every lead Jarvis adds is marked created_by='jarvis'
and every touch carries the person it was logged for.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import accounts, broker, leads_source, leads_store

log = logging.getLogger("app.lead_tools")
broker_router = APIRouter(prefix="/api/broker")

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEMPS = {t.lower(): t for t in leads_store.TEMPERATURES if t}
MAX_LISTED = 50


def _err(status: int, error: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail, **extra})


def _brief(lead: Dict[str, Any]) -> Dict[str, Any]:
    return {"lead_id": lead["lead_id"], "name": lead.get("name"), "phone": lead.get("phone"),
            "email": lead.get("email"), "property": lead.get("property"), "status": lead.get("status"),
            "temperature": lead.get("temperature"), "next_follow_up": (lead.get("next_touch_at") or "")[:10],
            "notes": lead.get("notes"), "agent": leads_source._display_name(lead.get("owner_person"))}


def _scope(entry: Dict[str, Any]) -> Optional[str]:
    return None if entry.get("role") in ("owner", "admin") else str(entry["person_key"])


def _find(viewer: Optional[str], lead_id: str, match: str) -> List[Dict[str, Any]]:
    if lead_id:
        lead = leads_store.get(lead_id)
        ok = lead and not lead["archived"] and (viewer is None or lead.get("owner_person") == viewer)
        return [lead] if ok else []
    key = leads_store.normalise_name(match)
    if not key:
        return []
    mine = leads_store.query(owner_person=viewer)
    exact = [l for l in mine if leads_store.normalise_name(l.get("name") or "") == key]
    return exact or [l for l in mine if key in leads_store.normalise_name(l.get("name") or "")]


def _add(person: str, viewer: Optional[str], body: Dict[str, Any]):
    name = str(body.get("name") or "").strip()[:200]
    if not name:
        return _err(422, "bad_request", "A lead needs a name.")
    owner = person
    if body.get("agent"):
        if viewer is not None:
            return _err(403, "not_allowed", "Only an admin or the owner can give a lead to someone else.")
        owner = leads_source.person_key_for(str(body["agent"])) or ""
        if not owner:
            return _err(422, "unknown_agent", "No one on the team goes by that name.")
    nxt = str(body.get("next_follow_up") or "")
    if nxt and not DATE.match(nxt):
        return _err(422, "bad_request", "next_follow_up must be YYYY-MM-DD.")
    temp = TEMPS.get(str(body.get("temperature") or "").strip().lower(), "")
    email = str(body.get("email") or "").strip()[:320]
    phone = str(body.get("phone") or "").strip()[:64]

    if not body.get("add_anyway"):
        seen = {l["lead_id"]: l for l in (leads_store.find_by_email(email) + leads_store.find_by_phone(phone)
                                          + leads_store.find_by_name(name))}
        if seen:
            broker._bump("lead_tool_duplicate")
            yours = [l for l in seen.values() if viewer is None or l.get("owner_person") == viewer]
            return _err(409, "duplicate", "A lead like this is already in the tracker.",
                        matches=[_brief(l) for l in yours][:5],
                        others=len(seen) - len(yours))

    fields = {k: v for k, v in {
        "email": email, "phone": phone,
        "property": str(body.get("property") or "").strip()[:300],
        "price_point": str(body.get("price_point") or "").strip()[:64],
        "source": str(body.get("source") or "").strip()[:120] or "Open house",
        "notes": str(body.get("notes") or "").strip()[:4000],
        "temperature": temp, "next_touch_at": nxt,
    }.items() if v}
    lead = leads_store.create_with_cadence(name, by=person, created_by="jarvis", owner_person=owner, **fields)
    broker._bump("lead_tool_added")
    return {"ok": True, "lead": _brief(lead)}


def _log(person: str, viewer: Optional[str], body: Dict[str, Any]):
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in leads_store.CONTACT_KINDS + ("note",):
        return _err(422, "bad_request", "kind must be call, text, email, met, showing or note.")
    nxt = str(body.get("next_follow_up") or "")
    if nxt and not DATE.match(nxt):
        return _err(422, "bad_request", "next_follow_up must be YYYY-MM-DD.")
    found = _find(viewer, str(body.get("lead_id") or ""), str(body.get("match") or ""))
    if not found:
        return _err(404, "not_found", "No lead of theirs matches that.")
    if len(found) > 1:
        return _err(409, "ambiguous", "More than one lead matches.", matches=[_brief(l) for l in found[:10]])
    res = leads_store.log_contact(found[0]["lead_id"], person, kind,
                                  note=str(body.get("note") or "")[:4000],
                                  reached=body.get("reached") is not False, next_touch_at=nxt or None)
    broker._bump("lead_tool_logged")
    return {"ok": True, "lead": _brief(res["lead"])}


def _followups(person: str, viewer: Optional[str], body: Dict[str, Any]):
    today = leads_store.today_local()
    owner = None if (viewer is None and body.get("everyone")) else person
    rows = leads_store.due(owner_person=owner, today=today)
    day = today.isoformat()
    overdue = [_brief(l) for l in rows if (l.get("next_touch_at") or "")[:10] < day]
    due_today = [_brief(l) for l in rows if (l.get("next_touch_at") or "")[:10] == day]
    return {"ok": True, "today": day, "overdue": overdue[:MAX_LISTED], "due_today": due_today[:MAX_LISTED],
            "total": len(rows)}


OPS = {"add": _add, "log": _log, "followups": _followups}


@broker_router.post("/leads")
async def post_broker_leads(request: Request):
    broker._authorize(request)
    body = await broker._json_body(request)
    entry = broker._turn_person(body.get("ref"))
    if entry is None:
        return _err(403, "no_turn", "No running chat turn.")
    person = str(entry["person_key"])
    if not accounts.feature_on(person, "leads"):
        broker._bump("feature_refused_leads")
        return _err(403, "feature_off", "Leads are switched off for this person.")
    if leads_source.source() != "store":
        return _err(409, "sheet_backed", "This tenant's leads live in a Google Sheet.")
    op = OPS.get(str(body.get("op") or ""))
    if op is None:
        return _err(400, "bad_request", "op must be add, log or followups.")
    try:
        return op(person, _scope(entry), body)
    except leads_store.LeadError as exc:
        return _err(422, "bad_request", str(exc))
