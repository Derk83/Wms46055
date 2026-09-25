"""Session-isolated guided inventory demo.

This module intentionally never imports inventory models. Every fictional quantity,
workflow event, and completion flag is stored in the authenticated user's session.
"""

from copy import deepcopy

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST


SESSION_KEY = "inventory_guided_demo_v1"

DEMO_ITEMS = (
    {
        "part_number": "DEMO-1001",
        "name": "Safety Glasses, Clear",
        "category": "Safety",
        "quantity": 24,
        "threshold": 10,
        "bin": "A-01-01",
        "unit": "each",
    },
    {
        "part_number": "DEMO-1002",
        "name": "Nitrile Work Gloves",
        "category": "Safety",
        "quantity": 8,
        "threshold": 10,
        "bin": "A-01-02",
        "unit": "box",
    },
    {
        "part_number": "DEMO-2001",
        "name": "Cable Labels, White",
        "category": "Consumables",
        "quantity": 40,
        "threshold": 12,
        "bin": "B-02-04",
        "unit": "pack",
    },
    {
        "part_number": "DEMO-2002",
        "name": "Hook-and-Loop Roll",
        "category": "Consumables",
        "quantity": 18,
        "threshold": 8,
        "bin": "B-02-05",
        "unit": "roll",
    },
    {
        "part_number": "DEMO-3001",
        "name": "Compact Flashlight",
        "category": "Tools",
        "quantity": 14,
        "threshold": 5,
        "bin": "C-01-03",
        "unit": "each",
    },
    {
        "part_number": "DEMO-3002",
        "name": "25 ft Measuring Tape",
        "category": "Tools",
        "quantity": 20,
        "threshold": 6,
        "bin": "C-01-04",
        "unit": "each",
    },
)

GUIDE_STEPS = {
    1: {
        "label": "Explore inventory",
        "title": "Find a low-stock item",
        "description": "Locate the item that needs attention and confirm its warehouse bin.",
        "task": "Select DEMO-1002, Nitrile Work Gloves in bin A-01-02.",
    },
    2: {
        "label": "Receive stock",
        "title": "Receive an inbound shipment",
        "description": "Add 12 fictional boxes to the selected item and watch its stock status update.",
        "task": "Receive 12 boxes of DEMO-1002.",
        "action": "receive_stock",
        "button": "Receive 12 boxes",
    },
    3: {
        "label": "Create a request",
        "title": "Reserve requested material",
        "description": "Create a fictional material request. The demo mirrors the live reservation behavior without writing to production.",
        "task": "Request 3 packs of DEMO-2001 Cable Labels.",
        "action": "create_request",
        "button": "Create demo request",
    },
    4: {
        "label": "Fulfill the pick",
        "title": "Complete the pick ticket",
        "description": "Confirm the reserved quantity and complete the fictional pick workflow.",
        "task": "Fulfill demo ticket DEMO-PT-001 for 3 packs.",
        "action": "fulfill_pick",
        "button": "Fulfill demo ticket",
    },
    5: {
        "label": "Review audit trail",
        "title": "Review the demo activity",
        "description": "Verify the receive, reservation, and fulfillment entries before finishing.",
        "task": "Review the audit trail below, then complete the guided demo.",
        "action": "complete_demo",
        "button": "Complete demo",
    },
}


def _initial_state():
    return {
        "step": 1,
        "complete": False,
        "quantities": {item["part_number"]: item["quantity"] for item in DEMO_ITEMS},
        "events": [
            {
                "kind": "START",
                "description": "Fictional demo inventory loaded",
                "quantity": "—",
            }
        ],
        "ticket_status": "Not created",
    }


def _valid_state(state):
    if not isinstance(state, dict) or set(state) != {
        "step",
        "complete",
        "quantities",
        "events",
        "ticket_status",
    }:
        return False
    quantities = state["quantities"]
    expected_parts = {item["part_number"] for item in DEMO_ITEMS}
    if not isinstance(state["step"], int) or isinstance(state["step"], bool):
        return False
    if state["step"] not in GUIDE_STEPS or not isinstance(state["complete"], bool):
        return False
    if not isinstance(quantities, dict) or set(quantities) != expected_parts:
        return False
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in quantities.values()):
        return False
    if state["ticket_status"] not in {"Not created", "Open", "Picked"}:
        return False
    events = state["events"]
    if not isinstance(events, list) or not events:
        return False
    return all(
        isinstance(event, dict)
        and set(event) == {"kind", "description", "quantity"}
        and all(isinstance(value, str) for value in event.values())
        for event in events
    )


def _state(request):
    state = request.session.get(SESSION_KEY)
    if not _valid_state(state):
        state = _initial_state()
    return deepcopy(state)


def _save(request, state):
    request.session[SESSION_KEY] = state
    request.session.modified = True


def _require_warehouse(request):
    if not getattr(request, "is_wms_host", False):
        raise Http404


def _inventory_demo_response(request):
    state = _state(request)
    items = []
    for source in DEMO_ITEMS:
        item = dict(source)
        item["quantity"] = state["quantities"][item["part_number"]]
        item["is_low"] = item["quantity"] <= item["threshold"]
        item["is_target"] = item["part_number"] == "DEMO-1002" and state["step"] == 1
        items.append(item)

    step = min(max(int(state["step"]), 1), 5)
    context = {
        "items": items,
        "step": step,
        "complete": bool(state["complete"]),
        "guide": GUIDE_STEPS[step],
        "guide_steps": GUIDE_STEPS,
        "progress_percent": 100 if state["complete"] else step * 20,
        "total_quantity": sum(item["quantity"] for item in items),
        "low_stock_count": sum(item["is_low"] for item in items),
        "events": list(reversed(state["events"])) if step == 5 or state["complete"] else state["events"],
        "demo_ticket_status": state["ticket_status"],
    }
    return render(request, "inventory/demo/workspace.html", context)


@login_required
@require_GET
def inventory_demo(request):
    _require_warehouse(request)
    request.is_training_page = True
    return _inventory_demo_response(request)


@require_GET
def public_inventory_demo(request):
    if not getattr(request, "is_demo_host", False):
        raise Http404
    request.is_training_page = True
    return _inventory_demo_response(request)


@require_POST
def _inventory_demo_action_response(request, redirect_name):
    action = request.POST.get("action", "").strip()
    if action == "reset":
        _save(request, _initial_state())
        messages.success(request, "The fictional demo inventory was reset.")
        return redirect(redirect_name)

    state = _state(request)
    expected = {
        1: "select_item",
        2: "receive_stock",
        3: "create_request",
        4: "fulfill_pick",
        5: "complete_demo",
    }.get(state["step"])
    if state["complete"] or action != expected:
        return HttpResponseBadRequest("Complete the current guided step first.")

    if action == "select_item":
        if request.POST.get("part_number") != "DEMO-1002":
            return HttpResponseBadRequest("Select the low-stock gloves to continue.")
        state["events"].append(
            {
                "kind": "VIEW",
                "description": "Located DEMO-1002 in bin A-01-02",
                "quantity": "8 boxes",
            }
        )
        state["step"] = 2
        messages.success(request, "Low-stock item found. Continue to receiving.")
    elif action == "receive_stock":
        state["quantities"]["DEMO-1002"] += 12
        state["events"].append(
            {
                "kind": "RECEIVE",
                "description": "Received DEMO-1002 Nitrile Work Gloves",
                "quantity": "+12 boxes",
            }
        )
        state["step"] = 3
        messages.success(request, "Demo receipt posted. On-hand stock is now 20 boxes.")
    elif action == "create_request":
        state["quantities"]["DEMO-2001"] -= 3
        state["ticket_status"] = "Open"
        state["events"].append(
            {
                "kind": "REQUEST",
                "description": "Created DEMO-MR-001 and reserved Cable Labels",
                "quantity": "-3 packs",
            }
        )
        state["step"] = 4
        messages.success(request, "Demo request and pick ticket created.")
    elif action == "fulfill_pick":
        state["ticket_status"] = "Picked"
        state["events"].append(
            {
                "kind": "PICK",
                "description": "Fulfilled DEMO-PT-001 with picker and QA confirmation",
                "quantity": "3 packs",
            }
        )
        state["step"] = 5
        messages.success(request, "Demo pick fulfilled. Review the audit trail.")
    elif action == "complete_demo":
        state["complete"] = True
        state["events"].append(
            {
                "kind": "COMPLETE",
                "description": "Guided warehouse demo completed",
                "quantity": "—",
            }
        )
        messages.success(request, "Demo complete. No production records were changed.")

    _save(request, state)
    return redirect(redirect_name)


@login_required
@require_POST
def inventory_demo_action(request):
    _require_warehouse(request)
    request.is_training_page = True
    return _inventory_demo_action_response(request, "inventory_demo")


@require_POST
def public_inventory_demo_action(request):
    if not getattr(request, "is_demo_host", False):
        raise Http404
    request.is_training_page = True
    return _inventory_demo_action_response(request, "inventory_demo")
