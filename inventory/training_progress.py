"""Sequential, server-validated fictional exercises stored only in browser sessions."""
from copy import deepcopy
import re
import secrets
from datetime import date

from .training_exercises import EQUIPMENT_ITEMS, REQUEST_ITEMS, exercise_for, validate_exercise

SESSION_KEY = "rpl_sequential_training_v1"
SESSION_VERSION = 2
GUIDES = ("material-requests", "equipment-requests")


class InvalidTrainingProgress(ValueError):
    """Malformed, incorrect, or out-of-order training submission."""


def _fresh_state():
    return {"version": SESSION_VERSION, "courses": {}, "drafts": {}}


def _valid_draft(slug, count, draft):
    if not isinstance(draft, dict) or type(count) is not int or not 1 <= count <= 5:
        return False
    keys = {"reference", "destination", "needed"}
    if slug == "equipment-requests":
        keys |= {"purpose", "priority"}
    else:
        keys |= {"requester", "delivery_time", "urgency"}
    if count >= 2:
        keys |= {"item", "quantity"}
    if count >= 3:
        keys |= {"allocated", "disposition"}
    if count >= 4:
        keys.add("number")
    catalog = REQUEST_ITEMS if slug == "material-requests" else EQUIPMENT_ITEMS
    try:
        if set(draft) != keys or not re.fullmatch(r"[A-Z]{2,6}-[0-9]{2,6}", draft["reference"]):
            return False
        if draft["destination"] not in ("Dock 2", "Field Office") or not isinstance(draft["needed"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", draft["needed"]):
            return False
        date.fromisoformat(draft["needed"])
        if slug == "equipment-requests" and (draft["purpose"] not in ("scheduled work", "temporary replacement") or draft["priority"] not in ("routine", "urgent")):
            return False
        if slug == "material-requests" and (draft["requester"] not in ("Training Operator", "Demo Specialist") or draft["delivery_time"] not in ("09:00", "14:00") or draft["urgency"] not in ("routine", "urgent")):
            return False
        if count >= 2 and (draft["item"] not in catalog or type(draft["quantity"]) is not int or not 1 <= draft["quantity"] <= 20):
            return False
        if count >= 3 and (type(draft["allocated"]) is not int or draft["allocated"] != min(draft["quantity"], catalog[draft["item"]]) or draft["disposition"] not in (("none",) if draft["quantity"] <= catalog[draft["item"]] else (("cancel", "backorder", "procurement") if slug == "material-requests" else ("waitlist", "reduce")))):
            return False
        if count >= 4 and not re.fullmatch(r"(?:MR|ER)-[A-F0-9]{12}", draft["number"]):
            return False
    except (TypeError, KeyError, ValueError):
        return False
    return True


def _load_state(request):
    state = request.session.get(SESSION_KEY)
    if not isinstance(state, dict) or set(state) != {"version", "courses", "drafts"} or state["version"] != SESSION_VERSION or not isinstance(state["courses"], dict) or not isinstance(state["drafts"], dict):
        return _fresh_state()
    for key, count in state["courses"].items():
        if not isinstance(key, str) or type(count) is not int or not 0 <= count <= 5:
            return _fresh_state()
    for key, draft in state["drafts"].items():
        slug = key.split(":")[-1] if isinstance(key, str) else ""
        if slug not in GUIDES or not _valid_draft(slug, state["courses"].get(key), draft):
            return _fresh_state()
    for key, count in state["courses"].items():
        if key.split(":")[-1] in GUIDES and count > 0 and key not in state["drafts"]:
            return _fresh_state()
    return deepcopy(state)


def completed_task_count(request, namespace, slug, total_tasks):
    count = _load_state(request)["courses"].get(f"{namespace}:{slug}", 0)
    return count if count <= total_tasks else 0


def course_draft(request, namespace, slug):
    return _load_state(request)["drafts"].get(f"{namespace}:{slug}", {})


def apply_progress_action(request, namespace, slug, total_tasks):
    state = _load_state(request)
    key = f"{namespace}:{slug}"
    completed = completed_task_count(request, namespace, slug, total_tasks)
    action = request.POST.get("action", "")
    if action == "reset":
        if set(request.POST) - {"csrfmiddlewaretoken", "action"}:
            raise InvalidTrainingProgress("Invalid reset fields.")
        state["courses"].pop(key, None)
        state["drafts"].pop(key, None)
        request.session[SESSION_KEY] = state
        request.session.modified = True
        return 0
    if action != "complete" or completed >= total_tasks:
        raise InvalidTrainingProgress("This training action is not available.")
    if request.POST.get("step") != str(completed + 1):
        raise InvalidTrainingProgress("Complete the current task before continuing.")
    draft = state["drafts"].get(key, {})
    try:
        updates = validate_exercise(slug, completed + 1, request.POST, draft)
    except (ValueError, KeyError, IndexError) as exc:
        raise InvalidTrainingProgress(str(exc) if isinstance(exc, ValueError) else "Invalid practice state; reset this module.") from exc
    if slug in GUIDES:
        draft.update(updates)
        if completed + 1 == 4:
            draft["number"] = ("MR" if slug == "material-requests" else "ER") + "-" + secrets.token_hex(6).upper()
        state["drafts"][key] = draft
    state["courses"][key] = completed + 1
    request.session[SESSION_KEY] = state
    request.session.modified = True
    return completed + 1


def progress_context(steps, completed, default_workspace_path=None, slug=None, draft=None, selected_tab=None):
    if slug is None:
        from .training_catalog import MATERIAL_REQUEST_GUIDE
        if steps is MATERIAL_REQUEST_GUIDE["steps"]:
            slug = "material-requests"
    total = len(steps)
    tasks = []
    for index, step in enumerate(steps, start=1):
        title, body = step[:2]
        status = "complete" if index <= completed else "current" if index == completed + 1 else "locked"
        tasks.append({
            "number": index, "title": title, "body": body,
            "workspace_path": step[2] if len(step) > 2 else default_workspace_path,
            "status": status,
            "exercise": exercise_for(slug, index, draft) if status == "current" else None,
        })
    is_complete = completed == total
    # Navigation may revisit a finished exercise, never unlock a future one.
    current_tab = total if is_complete else completed + 1
    if selected_tab is not None and len(selected_tab) <= 3 and selected_tab.isascii() and selected_tab.isdecimal():
        requested_tab = int(selected_tab)
        if 1 <= requested_tab <= completed:
            current_tab = requested_tab
    return {
        "training_tasks": tasks, "completed_tasks": completed, "total_tasks": total,
        "current_task_number": None if is_complete else completed + 1,
        "selected_training_tab": current_tab,
        "progress_percent": (completed * 100 // total) if total else 100,
        "training_complete": is_complete,
        "fictional_number": draft.get("number") if draft else None,
        "fictional_draft": draft if draft else None,
    }
