# Interactive training exercises — 2026-09-25

## What changed
- Replaced one-click progress in the fictional inventory demo with required, validated receiving, material-request, picking and QA, and audit-trail activities. Creating a practice request now asks for requester, destination, item, and quantity before generating a fictional request/ticket in the isolated session.
- Added server-validated, step-by-step practice questions and scenario inputs to public Warehouse, Equipment, and requester training modules. Later steps remain locked until the current activity passes validation.
- Material and equipment requester guides now build and track fictional practice requests, without submitting to operational request or inventory tables. Protected material guide uses the same fictional exercise flow.
- Clarified public training instructions so no one is directed to enter practice data into operational WMS. Demo categories use Supplies and Equipment/Tools.

## Verification and release
- Full suite: 769 passed, 2 skipped; Django check, migration check, compilation, JavaScript syntax, and diff checks passed. Focused follow-up: 114 passed.
- Independent read-only review completed; session and choice-order findings resolved.
- Deployed by collecting static assets and reloading `ppe-inventory.service` without schema changes. Live demo and both public requester guide URLs returned HTTPS 200. In a browser, an empty demo request returned 400 without advancing; submitting valid fictional request fields created DEMO-MR-001 and advanced to the pick activity. Operational hosts remained reachable.
- Code commits: `ad34955` and `50e6cd0`.
