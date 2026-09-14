# Organized desktop header and warehouse search

**Date:** 2026-09-13

## Summary

Implemented the approved hybrid header direction: Option B's organized two-row desktop structure with Option C's priority navigation. The established WMS palette, branding, permissions, request portal, and approved iPad/mobile drawer remain intact.

## Changes

- Organized desktop utilities into a dedicated top row with the RPL Warehouse brand, functional warehouse search, restrained utility controls, and a grouped account menu.
- Added a focused second navigation row with one-click access to Inventory, Tickets, Requests, and Receiving.
- Moved lower-frequency Insights, Tools, and Admin destinations into a permission-aware More menu.
- Added keyboard-accessible More and account menus with `aria-expanded`, Escape handling, outside-click dismissal, and breakpoint cleanup.
- Added permission-aware global search across inventory, pick tickets, and material requests.
- Preserved ticket and request visibility rules so search cannot reveal records unavailable to the signed-in user.
- Preserved the approved 390px iPad drawer and 50px mobile navigation rows.
- Kept install, notification, and theme controls available at iPad/mobile widths.
- Preserved the request portal's navigation and notification center.
- Added regression coverage for header structure, responsive rules, menu behavior, search results, search permissions, and request-portal notifications.

## Visual verification

The actual Django template, production CSS, and JavaScript were rendered through the isolated audit environment:

- 1280×960 desktop: 60px utility row, 45px priority row, working More and account menus, functional search, and no horizontal overflow.
- 1024×768 iPad: desktop rows hidden, approved 390px drawer retained, 50px navigation rows retained, and no horizontal overflow.

## Release verification

- 393 Django tests passed.
- 42 subtests passed.
- Django system check passed.
- Migration drift check passed.
- JavaScript syntax and Git diff checks passed.
- Independent correctness review returned PASS.
- Production static CSS hash matched the committed source after `collectstatic`.
- Gunicorn accepted the HUP and remained active.
- Authenticated production render verified desktop priority navigation, More/account menus, functional search, and zero horizontal overflow.
- Authenticated 1024px production render retained the 390px drawer, 50px rows, notification control, and theme control.
- Live browser console reported no errors.

## Phone header follow-up

After reviewing the production header at a 390px phone viewport, the compact header was simplified to the approved phone arrangement:

- Logo remains at the left.
- Notification bell remains directly accessible.
- Hamburger is the final control at the far right.
- Theme and install controls move into the WMS drawer on screens up to 600px.
- The 601–1120px iPad header remains unchanged.
- The request portal header remains unchanged.
- Drawer navigation rows remain 50px with no horizontal overflow.

The corrected real Django render was verified at 390×844 in both closed-header and open-drawer states. Theme switching was exercised successfully from inside the drawer.

Production verification confirmed the same 390px layout after deployment, with the bell before the far-right hamburger, a fully opened 320px drawer, theme control inside the drawer, matching live static hashes, and no browser-console errors.
