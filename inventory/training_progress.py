"""Session-only progress for sequential training tasks.

This module deliberately does not import operational models. Course progress is stored
in the browser session and completion notes are validated but never kept.
"""

from copy import deepcopy


SESSION_KEY = "rpl_sequential_training_v1"
SESSION_VERSION = 1
MAX_EVIDENCE_LENGTH = 500


class InvalidTrainingProgress(ValueError):
    """Raised when a task completion request is malformed or out of order."""


def _fresh_state():
    return {"version": SESSION_VERSION, "courses": {}}


def _load_state(request):
    state = request.session.get(SESSION_KEY)
    if (
        not isinstance(state, dict)
        or state.get("version") != SESSION_VERSION
        or not isinstance(state.get("courses"), dict)
        or any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in state.get("courses", {}).items()
        )
    ):
        state = _fresh_state()
    return deepcopy(state)


def completed_task_count(request, namespace, slug, total_tasks):
    """Return validated completion count for one course, resetting stale state."""
    state = _load_state(request)
    key = f"{namespace}:{slug}"
    completed = state["courses"].get(key, 0)
    if completed > total_tasks:
        return 0
    return completed


def apply_progress_action(request, namespace, slug, total_tasks):
    """Apply a reset or the single currently allowed completion action."""
    state = _load_state(request)
    key = f"{namespace}:{slug}"
    completed = completed_task_count(request, namespace, slug, total_tasks)
    action = request.POST.get("action", "")

    if action == "reset":
        state = _load_state(request)
        state["courses"].pop(key, None)
        request.session[SESSION_KEY] = state
        request.session.modified = True
        return 0

    if action != "complete" or completed >= total_tasks:
        raise InvalidTrainingProgress("This training action is not available.")

    try:
        submitted_step = int(request.POST.get("step", ""))
    except (TypeError, ValueError) as exc:
        raise InvalidTrainingProgress("Select the current task.") from exc

    expected_step = completed + 1
    evidence = request.POST.get("evidence", "").strip()
    if submitted_step != expected_step:
        raise InvalidTrainingProgress("Complete the current task before continuing.")
    if request.POST.get("confirm") != "yes":
        raise InvalidTrainingProgress("Confirm that you completed the task.")
    if len(evidence) < 3 or len(evidence) > MAX_EVIDENCE_LENGTH:
        raise InvalidTrainingProgress("Enter a brief completion note of 3 to 500 characters.")

    state = _load_state(request)
    state["courses"][key] = expected_step
    request.session[SESSION_KEY] = state
    request.session.modified = True
    return expected_step


def progress_context(steps, completed, default_workspace_path=None):
    """Build display state while keeping future task instructions locked."""
    total = len(steps)
    tasks = []
    for index, step in enumerate(steps, start=1):
        title, body = step[:2]
        workspace_path = step[2] if len(step) > 2 else default_workspace_path
        if index <= completed:
            status = "complete"
        elif index == completed + 1:
            status = "current"
        else:
            status = "locked"
        tasks.append({
            "number": index,
            "title": title,
            "body": body,
            "workspace_path": workspace_path,
            "status": status,
        })

    is_complete = completed == total
    return {
        "training_tasks": tasks,
        "completed_tasks": completed,
        "total_tasks": total,
        "current_task_number": None if is_complete else completed + 1,
        "progress_percent": (completed * 100 // total) if total else 100,
        "training_complete": is_complete,
    }
