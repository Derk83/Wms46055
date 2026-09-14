# Controlled Material-Request Portal Onboarding

**Released:** 2026-09-14  
**Feature commit:** `4b164b3`  
**Public portal:** `https://requests.rplwms.com/`

## Summary

Added a controlled self-service onboarding workflow for the material-request portal. Public visitors can request access without seeing warehouse data. Only exact, normalized `@blackbox.com` email addresses are accepted, and access is not created until email ownership is verified and an authorized manager approves the request.

## Applicant workflow

- Public Black Box access-request splash page
- Existing-user sign-in link
- Registration fields for:
  - Full name
  - Position
  - Exact `@blackbox.com` email
  - Contact number
  - Company/department
  - Project/jobsite
  - Supervisor/sponsor
  - Reason for access
  - Accuracy/privacy acknowledgment
- Generic public responses that do not reveal whether an account or request already exists
- Email verification before manager review
- One-time, expiring account-setup link after approval
- Verified email becomes both username and email address
- Django password validation before activation
- More-information request and resubmission flow

## Manager controls

- Permission-gated access-request queue and pending dashboard badge
- Request detail and audit history
- Approve, deny, request-more-information, resend-setup, and revoke-setup actions
- Optional temporary access expiration date
- Reviewer identity, timestamps, notes, and lifecycle events retained for auditing

## Security and privacy

- Public onboarding routes isolated to `requests.rplwms.com`
- Reviewer routes isolated to the authenticated WMS
- Exact corporate-domain validation
- Cryptographically random one-time tokens stored only as SHA-256 digests
- Token purpose, expiration, consumption, and revocation enforcement
- Database-backed IP/email throttling and honeypot protection
- CSRF protection and no-store/no-referrer controls on sensitive pages
- Duplicate-safe transactional request handling
- Automatic assignment only through the managed `material_requests` role
- New users are inactive, non-staff, non-superusers until password setup completes
- Authenticated requester email is bound to the verified account identity
- Synchronous temporary-access expiration on both WMS and request hosts
- Fail-closed handling for concurrent requests during account expiration
- Automatic expiration, reminders, token revocation, and privacy-retention cleanup

## Deployment

- Applied migration `inventory.0046_portal_access_onboarding`
- Installed and enabled `ppe-portal-onboarding.timer`
- Lifecycle job scheduled daily at 05:15 America/Chicago
- Initial lifecycle run completed successfully: `expired=0 reminded=0 purged=0`
- Collected static files and reloaded Gunicorn with `HUP`
- Pre-migration database backup:
  - `/home/hermes/projects/ppe_inventory/db.sqlite3.pre-onboarding-20260914-053630.bak`

## Verification

- Focused concurrency/expiration regression: **3 passed**
- Complete project suite: **424 passed, 42 subtests passed**
- One unrelated existing Django email deprecation warning remains
- Django system check: no issues
- Migration drift check: no changes detected
- Git diff validation: clean
- Local and GitHub `master` revisions matched at `4b164b3`
- Live public root returned HTTP 200 with no-store caching
- Browser confirmed all access-request fields and existing-user sign-in link
- Existing-user login page links back to Request Access
- WMS root remained protected and redirected unauthenticated users to login
- Manager queue rejected unauthenticated access
- Request-host warehouse routes remained login-protected
- Lifecycle timer active; one-shot service last result successful

No live applicant record or warehouse transaction was created during verification.
