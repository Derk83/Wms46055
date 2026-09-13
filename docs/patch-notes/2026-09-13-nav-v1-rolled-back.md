# Patch notes — 2026-09-13 — Nav V1 rolled back

## Summary

The 2026-09-13 "refined pills" nav (V1) was rolled back after live review. Live site now
shows the pre-V1 nav baseline.

## Why rolled back

V1 was shipped without an end-to-end visual proof point before deploy. After seeing it on
a real screen (iPad / mobile), the visual was reported as unrefined / unprofessional —
bigger type, accent underlines, section dividers, and inline glyphs did not land as
intended in the actual rendered page. Rather than iterate further on a direction that was
not connecting, the change was reverted in full and a different direction will be taken
with proper visual verification (working vision pipeline + screenshot proof) before any
new UI work ships.

## What changed

**Reverted (back to pre-V1 baseline):**

- `.site-nav a` — 13px / weight 700 / 8px×10px padding / 8px radius (was 14px / 650 / 9px×13px / 10px)
- `.site-nav a:hover` — solid `--surface-2` background, color shifts to accent (was 8% accent-tint)
- `.site-nav a.is-current` / `[aria-current="page"]` — no box-shadow underline (was `inset 0 -2px 0 0 var(--accent)`)
- `.nav-section-label` — hidden on desktop (`display:none`); mobile keeps visible
- `.nav-section` dividers — solid `var(--border)` at full opacity (was 60% opacity)
- `.brand` — back to single `.brand span` subtitle; `.brand-text`, `.brand-primary`, `.brand-subtitle` removed
- `.nav-toggle-icon` — 17px wide, `.18s ease` animation (was 18px / `.22s ease`)
- Mobile slide-out — `370px` wide, links framed like form inputs (border + `--surface-2` background)
- Mobile active state — full accent border (was inset 3px left stripe + tinted background)
- 10 `<span class="ico">▦▤◫↓↻⌘◉⌗⊞⤓⚙</span>` glyphs removed from nav links

**No changes to:**

- View permissions / role gating
- Section structure (still 4 sections: Operations, Insights, Tools, Admin)
- Mobile breakpoint (still 1120px)
- PWA, theme toggle, notification center, push config, request portal integration
- Any other template, view, or JS file

## Verification

- `git log` shows the two revert commits on `master` and pushed to remote.
- Live CSS at `https://bbx.rplwms.com/static/inventory/css/app.css` contains none of the V1-only rules.
- Pre-V1 nav rules confirmed present (`font-size:13px`, `font-weight:700`, `padding:8px 10px`, `border-radius:8px`, no `.ico` rule, `.nav-section-label{display:none}` on desktop).
- `collectstatic` re-run; 143 files refreshed in `staticfiles/`.

## Lessons

- **Ship with a real visual proof point, not just structural verification.** V1 was verified
  by curl + grep + test client rendering — none of which catch "looks unprofessional to a
  human on an iPad." Until the vision pipeline is fixed, future UI work needs a different
  proof loop (you describe what you see, I describe what I see in code, we converge
  before any deploy).
- **Visual direction should be picked from a real comparison.** Picking V1 from a small
  text-thumbnail sketch is not enough signal to commit a visual direction.
- **If vision is broken, that's a block, not a "ship anyway."** V1 was deployed with
  vision down because I treated the visual proof step as optional. It is not.

## Next steps (not yet started)

- Fix or work around the broken vision pipeline (root cause: `openrouter.api_key` is empty
  in `~/.hermes/config.yaml`; vision calls return 401 "Missing Authentication header").
- Re-derive UI direction from your words, real reference sites, and/or a working
  vision-driven screenshot loop — not from the existing skill's spec alone.
- New direction gets a "see it before you ship it" gate: I describe what each rule will
  render to in pixels, you confirm or correct, only then does the change go into a PR.
