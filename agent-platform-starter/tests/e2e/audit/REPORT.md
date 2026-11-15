# Dashboard UI/UX audit (2026-10-28)

Headless Chromium against the preview server, at 1440×900, 1100×760, 768×1024 and 390×844, across every view.
Re-run: `tests/e2e/audit/start_server.sh` (port 8771, database `audit_ui`; restart before each run of a script
that changes state), then `node tests/e2e/audit/layout.js`, `interactions.js`, `W=390 H=844 node .../streaming.js`
and `crops.js`. Screenshots are in `shots/`. Status after the fix pass is in "Resolution" at the end.

## Broken
1. **Knowledge → fact "Open task" opens an invisible drawer and locks scrolling.** `#detail` sits inside the hidden
   `section#tasks`; `body.drawer-open` sets `overflow:hidden`.
2. **The model and agent selects are empty after a normal login.** `loadRoutingModels()` and `loadAgents()` run
   before login (`401 /ui/api/models`); `refresh()` doesn't call them.
3. **Unsaved Policy edits are silently discarded** when switching views or pressing Refresh, and there is no
   dirty indicator.
4. **Deploy can be started twice.** The button doesn't disable, and the 409 notice is overwritten by "Deploy started".
5. **The task drawer is `aria-modal` but doesn't trap focus.**
6. **Stop silently drops the queued follow-up**, and its text is lost.

## Confusing / missing feedback
7. **The single `#notice` bar** is off-screen for forms far down the page, never cleared, and shifts the chat
   layout (at 390, Send/Stop end up below the fold).
8. **Header Refresh** doesn't reload the current view (Queue, Knowledge, Agents, Infrastructure) but says "Refreshed".
9. **Knowledge search** ignores Enter, and an empty result says "No entries yet."
10. **The Freshness filter counts** don't update after a fact is deleted.
11. **Knowledge and Agents edit mode** has weak cues: no "Cancel edit", nothing highlighted, and a page jump.
12. **An agent with no role** shows a leading " | ".
13. **Infrastructure "not configured"** shows empty "Nodes"/"Guests" headings and a 503.
14. **A third message while one is queued** is silently ignored.
15. **Returning to a running conversation** loses the tool steps from the live bubble.
16. **`_Stopped before the agent finished._`** renders literally, because the markdown renderer has no `_italic_`.
17. **A running rollback** shows the full 40-character SHA as its ref.

## Layout
18. **Task table** at 1100/768: the Request column is crushed and Created is cut off.
19. **Deployments table at 390:** Status and Roll back are only reachable by sideways scrolling.
20. **Policy level selects** are truncated at 390 and 1100.
21. **Phone nav:** Infrastructure is off-screen, accessible names are glyphs, and there is no `aria-current`.
22. **On phone the composer** takes about 25% of the screen, and Stop/Queue sit at the bottom edge.
23. **Mixed inline and stacked labels** misalign form rows.
24. **At 1100 the message pane** is only about 450×330.

## Cosmetic / accessibility
- **Unlabelled controls:** the Policy level selects and compose inputs have no labels.
- **Touch targets:** several are small at ≤768.
- **Contrast:** `.nav-label` is below AA.
- **Login focus:** focus stays on `body` after login.
- **"↓ Latest":** overlaps the composer border at 390.
- **Paging:** "Older →" keeps the scroll position at the bottom of the page.

## Live/streaming (as seen)
- **Sequence:** Thinking… → Running diagnostic · target (spinner, steps) → Reviewing the results… → Writing…
  (cursor) → final message with collapsed steps and Copy.
- **Scrolling:** scrolling up keeps the position, and "↓ Latest" appears.
- **Problems:** the per-step seconds stay at "0 s"; see also #6, #7, #15, #16 and #22.
