"""Static, fictional practice fixtures. No operational models or user text are stored."""
from datetime import date
from hashlib import sha256


def choice(label, answer, *options):
    return {"label": label, "kind": "choice", "answer": answer, "options": options}


def number(label, answer):
    return {"label": label, "kind": "number", "answer": answer}


# Each decision is tied to the displayed fictional case; wrong workflow choices
# cannot advance. Future answers live only here, never in rendered hidden inputs.
EXERCISES = {
    "inventory-locations-scanning": (
        ("Search RPL-DEMO-104; result RPL-DEMO-104 is a nitrile glove, result RPL-DEMO-140 is a filter. Which part matches?", choice("Part number", "RPL-DEMO-104", "RPL-DEMO-104", "RPL-DEMO-140")),
        ("The matching label reads Building A / Room 2 / Rack 4 / Section B / Bin 7. Which bin should you visit?", choice("Bin", "B7", "B7", "B8")),
        ("On hand 12, active, minimum 10; six are needed. How many units remain after a controlled pick?", number("Remaining units", 6)),
        ("The scanned label resolves to RPL-DEMO-140, not the selected RPL-DEMO-104. What should happen?", choice("Label action", "quarantine", "quarantine", "apply")),
    ),
    "receiving": (
        ("Packing slip PS-41 lists PO-204 and vendor North; delivery shows PO-204/vendor North, quantity 7 (PO ordered 10). Which source quantity is received?", number("Physical units", 7)),
        ("Catalog has RPL-DEMO-104 and RPL-DEMO-140; the shipment includes RPL-DEMO-104. Choose its matching catalog entry.", choice("Catalog match", "RPL-DEMO-104", "RPL-DEMO-104", "create-new")),
        ("Ticket shows 7 gloves and 3 filters physically received. What is the ticket total before posting once?", number("Total received units", 10)),
        ("History shows glove +7 and filter +3 against PS-41. Which source reference should the receipt link to?", choice("Packing slip", "PS-41", "PS-41", "PS-14")),
    ),
    "pick-tickets-qa": (
        ("Ticket PT-72 belongs to you and PT-73 belongs to another picker. Which ticket can you accept?", choice("Assigned ticket", "PT-72", "PT-72", "PT-73")),
        ("PT-72 specifies bin B7; the same-looking box in bin B8 has a different part number. Which bin is correct?", choice("Pick bin", "B7", "B7", "B8")),
        ("Line requests 5, bin contains 4. Enter actual picked quantity and variance disposition.", number("Actual units", 4), choice("Variance", "short", "short", "exact")),
        ("Picker is Alex; checker is Sam. Who can independently QA the ticket?", choice("QA checker", "Sam", "Sam", "Alex")),
        ("After QA the ticket is Picked. Which stage comes next before Closed/Delivered?", choice("Next stage", "ready", "ready", "closed")),
    ),
    "material-request-processing": (
        ("MR-81 requires delivery to Dock 2 at 09:00 and three lines; MR-82 goes to Dock 3. Which request matches?", choice("Request", "MR-81", "MR-81", "MR-82")),
        ("MR-81 is unassigned and you are an eligible specialist. Choose the controlled ownership action.", choice("Ownership", "claim", "claim", "edit-owner")),
        ("After assignment to Sam, who must acknowledge before picking?", choice("Acknowledging specialist", "Sam", "Sam", "Alex")),
        ("The linked ticket reports 3 requested, 2 picked. Which actual quantity belongs on the linked ticket?", number("Picked units", 2)),
        ("The linked ticket passed QA and is ready at Dock 2. Which status tells the requester it can be delivered?", choice("Status", "ready", "ready", "closed")),
    ),
    "shortages-procurement": (
        ("A request needs 10, available stock is 6. Enter the largest safe allocation and unmet amount.", number("Allocate now", 6), number("Short units", 4)),
        ("Four missing units are required for next week's job and sourcing is needed. Choose their disposition.", choice("Disposition", "procurement", "procurement", "cancel", "backorder")),
        ("Backorder BO-41 is linked to request MR-81; BO-42 is unrelated. Which record tracks the remaining demand?", choice("Backorder", "BO-41", "BO-41", "BO-42")),
        ("Requisition PR-9 references BO-41 and quantity 4; PR-10 references BO-42. Which requisition follows this shortage?", choice("Requisition", "PR-9", "PR-9", "PR-10")),
        ("Four arrive; BO-41 still needs 4. Enter the quantity for supplemental fulfillment.", number("Supplemental units", 4)),
    ),
    "cycle-counts": (
        ("Assigned count CC-12 covers Rack 4; CC-13 covers Rack 5. Which count is in scope?", choice("Count", "CC-12", "CC-12", "CC-13")),
        ("Rack 4 physically has 8 units; the blind count screen must not reveal its expected 10. Enter the physical count.", number("Counted units", 8)),
        ("Expected 10, counted 8. How many fewer units require investigation?", number("Variance units", 2)),
        ("After a verified recount of 8, which controlled action records the adjustment?", choice("Adjustment path", "reconcile", "reconcile", "edit-item")),
    ),
    "transactions-reports": (
        ("Opening balance 20; issue -6; receipt +3. What is the ledger balance?", number("Closing balance", 17)),
        ("Transaction -6 links to PT-72; +3 links to RC-11. Which source explains the issue?", choice("Source", "PT-72", "PT-72", "RC-11")),
        ("Report shows 17 and ledger shows 17; filter by Rack 4 to investigate a different item. Which scope applies?", choice("Location filter", "Rack 4", "Rack 4", "Rack 5")),
        ("A receipt was posted twice. Which correction preserves an audited history?", choice("Correction", "reversal", "reversal", "rewrite-ledger")),
    ),
    "users-permissions": (
        ("Taylor changes from Warehouse manager to requester only. Which host should remain accessible?", choice("Retained host", "requests", "requests", "warehouse-manager")),
        ("Taylor needs equipment requester access, not manager access. Which group is least privilege?", choice("Equipment role", "requester", "requester", "manager")),
        ("An equipment requester must not export costs. Choose the sensitive capability to withhold.", choice("Withheld permission", "export-costs", "export-costs", "request-status")),
        ("Taylor has old signed custody records. Which access-removal method preserves attribution?", choice("Removal", "deactivate", "deactivate", "delete-history")),
        ("After the change, Taylor opens the equipment manager host. What result is expected?", choice("Boundary", "denied", "denied", "allowed")),
    ),
    "asset-register": (
        ("A scanner finds serial SN-204 already on asset A-12. Should you create a duplicate or open A-12?", choice("Identity action", "open-A-12", "open-A-12", "duplicate")),
        ("New asset tag EQ-205 / serial SN-205 is not found; choose its unique serial.", choice("New serial", "SN-205", "SN-205", "SN-204")),
        ("EQ-205 is currently checked out. Which path changes custody?", choice("Custody action", "checkout", "checkout", "generic-edit")),
        ("Printed EQ-205 label scans to EQ-250. What should happen before attachment?", choice("Label action", "replace-label", "replace-label", "attach")),
    ),
    "custody-returns": (
        ("EQ-205 is available; EQ-206 is held for another team. Which can be issued?", choice("Available asset", "EQ-205", "EQ-205", "EQ-206")),
        ("Checkout CO-7 issues EQ-205 to Team Blue; CO-8 is Team Red. Which checkout captures this handoff?", choice("Checkout", "CO-7", "CO-7", "CO-8")),
        ("Asset custody still shows Team Blue; a handoff to Team Green requires which record?", choice("Handoff path", "transfer", "transfer", "edit-asset")),
        ("EQ-205 returned with a damaged screen. Which condition should the return record carry?", choice("Return condition", "damaged", "damaged", "good")),
        ("After return, which record retains the prior Team Blue custody?", choice("History", "CO-7", "CO-7", "delete-checkout")),
    ),
    "reservations": (
        ("Team Blue needs EQ-205 from Oct 5 through Oct 8. Which window matches?", choice("Window", "Oct 5-8", "Oct 5-8", "Oct 6-9")),
        ("EQ-205 already has an approved Oct 6-7 hold. Which asset can be reserved for Oct 5-8 if EQ-207 is free?", choice("Asset", "EQ-207", "EQ-207", "EQ-205")),
        ("Approved reservation for EQ-207 starts Oct 5. What does approval create?", choice("Effect", "future-hold", "future-hold", "current-checkout")),
        ("On Oct 5 the team takes EQ-207. Which transition creates real custody?", choice("Transition", "linked-checkout", "linked-checkout", "status-only")),
    ),
    "maintenance-rentals": (
        ("EQ-301 service due today; rental EQ-302 due in 9 days. Which requires attention first?", choice("Priority asset", "EQ-301", "EQ-301", "EQ-302")),
        ("EQ-301 has an approved reservation tomorrow. What must be resolved before booking conflicting service?", choice("Conflict", "reservation", "reservation", "ignore")),
        ("Meter was 1200 at last service, 1300 now. What reading belongs on the completion record?", number("Current meter", 1300)),
        ("Rental line RL-8 expected return Oct 8; contract ends Oct 10. Which date drives the open-line obligation?", choice("Return deadline", "Oct 8", "Oct 8", "Oct 10")),
        ("EQ-301 was archived before service. What state may it retain after service completion?", choice("Final state", "archived", "archived", "available")),
    ),
    "request-queue": (
        ("Request ER-12 needs two devices at Dock 2; ER-13 needs one at Dock 3. Which record matches the demand?", choice("Request", "ER-12", "ER-12", "ER-13")),
        ("ER-12 is unassigned. Which action creates audited manager ownership?", choice("Assignment", "assign", "assign", "edit-requester")),
        ("Preferred EQ-205 is held; EQ-207 is available and matches the category. Which can be allocated?", choice("Asset", "EQ-207", "EQ-207", "EQ-205")),
        ("ER-12 has passed Review and approval but equipment is not staged. Which state is supported?", choice("State", "approved", "approved", "fulfilled")),
        ("Two devices have been issued. Which record establishes actual custody?", choice("Evidence", "checkout", "checkout", "preference")),
    ),
    "imports-reporting": (
        ("Workbook W-4 contains 5 rows; upload is staged. How many authoritative assets exist immediately from upload?", number("Created assets", 0)),
        ("Row 7 has serial SN-205 already used by EQ-205; row 8 has unique SN-208. Which row is safe to approve?", number("Unique row", 8)),
        ("Row 7 duplicates SN-205. Choose the safe resolution before approval.", choice("Resolution", "reject-duplicate", "reject-duplicate", "approve-duplicate")),
        ("Row 8 is pre-assigned. What additional evidence must approval create?", choice("Custody provenance", "baseline-checkout", "baseline-checkout", "edit-owner")),
        ("A user lacks cost permission. What must a report export do with costs?", choice("Cost columns", "omit", "omit", "include")),
    ),
}

# Request guides use actual typed entry, a fixed fictional catalog, and a
# sanitized cross-step draft. No names, emails, notes or personal text stored.
REQUEST_ITEMS = {"RPL-DEMO-104": 6, "RPL-DEMO-140": 12}
EQUIPMENT_ITEMS = {"laptop": 1, "radio": 3}


def guide_fields(slug, step, draft):
    material = slug == "material-requests"
    if step == 1:
        return (
            {"label": "Fictional work reference (e.g. JOB-204)", "kind": "reference", "name": "reference"},
            {"label": "Delivery destination", "kind": "choice", "name": "destination", "options": ("Dock 2", "Field Office")},
            {"label": "Needed date (today or later)", "kind": "date", "name": "needed"},
            {"label": "Fictional requester", "kind": "choice", "name": "requester", "options": ("Training Operator", "Demo Specialist")},
            {"label": "Required delivery time", "kind": "choice", "name": "delivery_time", "options": ("09:00", "14:00")},
            {"label": "Urgency", "kind": "choice", "name": "urgency", "options": ("routine", "urgent")},
        ) if material else (
            {"label": "Fictional project reference (e.g. JOB-204)", "kind": "reference", "name": "reference"},
            {"label": "Destination", "kind": "choice", "name": "destination", "options": ("Dock 2", "Field Office")},
            {"label": "Needed date (today or later)", "kind": "date", "name": "needed"},
            {"label": "Purpose", "kind": "choice", "name": "purpose", "options": ("scheduled work", "temporary replacement")},
            {"label": "Priority", "kind": "choice", "name": "priority", "options": ("routine", "urgent")},
        )
    if step == 2:
        catalog = REQUEST_ITEMS if material else EQUIPMENT_ITEMS
        return (
            {"label": "Fictional catalog item (available: " + ", ".join(f"{k}={v}" for k, v in catalog.items()) + ")", "kind": "choice", "name": "item", "options": tuple(catalog)},
            {"label": "Quantity requested (1–20)", "kind": "integer", "name": "quantity"},
        )
    if step == 3:
        available = (REQUEST_ITEMS if material else EQUIPMENT_ITEMS)[draft["item"]]
        if material:
            return (
                {"label": f"Available now: {available}. Units to allocate now", "kind": "integer", "name": "allocated"},
                {"label": "Remaining quantity disposition", "kind": "choice", "name": "disposition", "options": ("none", "cancel", "backorder", "procurement")},
            )
        return (
            {"label": f"Available assets: {available}. Units that could be allocated after manager review", "kind": "integer", "name": "allocated"},
            {"label": "Unallocated units disposition", "kind": "choice", "name": "disposition", "options": ("none", "waitlist", "reduce")},
        )
    if step == 4:
        return ({"label": "Enter the fictional work reference to review and submit this draft", "kind": "reference", "name": "review_reference"},)
    return ({"label": "Find your fictional request by its confirmation number", "kind": "tracking", "name": "tracking_number"},)


def exercise_for(slug, step, draft=None):
    if slug in ("material-requests", "equipment-requests"):
        return {"prompt": "Enter the fictional request data below. This practice does not submit to an operational system.", "fields": guide_fields(slug, step, draft or {})}
    prompt, *fields = EXERCISES[slug][step - 1]
    display_fields = []
    for i, field in enumerate(fields, 1):
        shown = {"name": f"answer_{i}", **field}
        if field["kind"] == "choice":
            options = field["options"]
            # Stable per exercise, but the correct choice is not always first.
            offset = sha256(f"{slug}:{step}:{i}".encode()).digest()[0] % len(options)
            shown["options"] = options[offset:] + options[:offset]
        display_fields.append(shown)
    return {"prompt": prompt, "fields": tuple(display_fields)}


def validate_exercise(slug, step, post, draft):
    """Return only allowlisted draft values; reject missing, conflicting and extra form values."""
    exercise = exercise_for(slug, step, draft)
    fields = exercise["fields"]
    allowed = {"csrfmiddlewaretoken", "action", "step"} | {f["name"] for f in fields}
    if set(post) - allowed or any(len(post.getlist(f["name"])) != 1 for f in fields):
        raise ValueError("Invalid practice fields.")
    values = {}
    for field in fields:
        value = post.get(field["name"], "")
        if not isinstance(value, str) or len(value) > 40 or value != value.strip():
            raise ValueError("Invalid practice value.")
        kind = field["kind"]
        if kind == "number" or kind == "integer":
            if not value.isascii() or not value.isdecimal() or len(value) > 6:
                raise ValueError("Enter a valid whole quantity.")
            parsed = int(value)
            if kind == "number" and parsed != field["answer"]:
                raise ValueError("Recheck the scenario quantities.")
            if kind == "integer" and not 0 <= parsed <= 20:
                raise ValueError("Quantity must be between 0 and 20.")
            values[field["name"]] = parsed
        elif kind == "choice":
            if value not in field["options"] or ("answer" in field and value != field["answer"]):
                raise ValueError("Recheck the scenario and choose the appropriate action.")
            values[field["name"]] = value
        elif kind == "reference":
            import re
            if not re.fullmatch(r"[A-Z]{2,6}-[0-9]{2,6}", value):
                raise ValueError("Use a fictional reference such as JOB-204.")
            values[field["name"]] = value
        elif kind == "tracking":
            import re
            if not re.fullmatch(r"(?:MR|ER)-[A-F0-9]{12}", value):
                raise ValueError("Enter the fictional confirmation number.")
            values[field["name"]] = value
        elif kind == "date":
            import re
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Enter a date in YYYY-MM-DD format.")
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Enter a valid date.") from exc
            if parsed < date.today() or parsed.year > date.today().year + 2:
                raise ValueError("Choose a date within the next two years.")
            values[field["name"]] = value
    if slug not in ("material-requests", "equipment-requests"):
        return {}
    material = slug == "material-requests"
    if step == 1:
        return values
    if step == 2:
        if not 1 <= values["quantity"] <= 20:
            raise ValueError("Request between 1 and 20 units.")
        return values
    if step == 3:
        available = (REQUEST_ITEMS if material else EQUIPMENT_ITEMS)[draft["item"]]
        expected = min(draft["quantity"], available)
        if values["allocated"] != expected:
            raise ValueError("Allocate only the available units.")
        short = draft["quantity"] > available
        if (values["disposition"] == "none") == short:
            raise ValueError("Choose a disposition for the shortage, or none when fully available.")
        return values
    if step == 4:
        if values["review_reference"] != draft["reference"]:
            raise ValueError("Review reference does not match your draft.")
        # Deterministic per-session number generated from a random session token,
        # not a model ID; impossible to predict from client-controlled fields.
        return {}
    if values["tracking_number"] != draft["number"]:
        raise ValueError("That fictional request number was not found in this practice session.")
    return {}
