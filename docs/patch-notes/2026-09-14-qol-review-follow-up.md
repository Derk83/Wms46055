# QoL Review Follow-up

**Released:** September 14, 2026
**Application commit:** `d4df4ea`

## Corrections

- Updated the Missing Location filter to include inventory with incomplete rack/section tuples when neither a building/room nor a usable bin is present.
- Connected inventory column preferences to mobile card fields and actions so mobile selections now match desktop behavior.
- Confirmed the previously reported global Escape-key issue was already corrected before the original QoL deployment.

## Verification

- Full gate: 492 tests and 45 subtests passed.
- Django checks, migration drift check, Python compilation, JavaScript syntax, and Git diff checks passed.
- No migrations were required.
- Both production hosts preserved the Missing Location query through authentication redirects.
- Production service remained active with zero unexpected restarts.
