# Homelab Agents — UI design system (v3)

Vanilla JS/CSS, no framework, no build step, no CDN. Files: `index.html` (shell + page markup), `style.css`
(design system + page rules), `app.js` (one script; the v3 shell is the `HLA_UI_V3` block at the end),
`fonts/` (Inter, SIL OFL, served by `/ui/fonts/{name}` and cached by the service worker), `sw.js`.

## 1. Tokens (`style.css` §1)

| Group | Tokens |
|---|---|
| Surfaces | `--bg` #080D14 · `--sidebar` #0C121C · `--surface` #111A26 · `--surface-2` · `--elevated` #162231 · `--hover` · `--active` |
| Lines | `--border` #223041 · `--border-soft` (default separators) · `--border-strong` |
| Text | `--text` #F0F5FA · `--text-2` #91A0AF · `--text-3` #647487 |
| Accent | `--accent` #27C7E8 · `--accent-hover` · `--accent-bg` · `--accent-line` · `--on-accent` (text on cyan) |
| Status | `--success` `--warning` `--danger` (+ `-bg` and `-line` variants) · `--violet` |
| Type | `--font` (Inter) · `--mono` (technical data only) · `--fs-xs` 11.5 · `--fs-sm` 12.5 · `--fs` 13.5 · `--fs-md` 15 · `--fs-lg` 17 · `--fs-xl` 22 · `--fs-2xl` 26 |
| Space | `--s1` 4 · `--s2` 8 · `--s3` 12 · `--s4` 16 · `--s5` 20 · `--s6` 24 · `--s7` 32 · `--s8` 48 |
| Radius | `--r-sm` 6 · `--r` 8 · `--r-lg` 12 · `--r-xl` 16 |
| Layout | `--side-w` 224 · `--rail-w` 64 · `--content-w` 1280 (1360 ≥1600px) |

Rules: hierarchy comes from background + spacing + type, not outlines (panels have no border). Cyan only for primary
actions, the active indicator and links. Status colours are muted pills; red only for real failures, amber for
"needs you". No uppercase/letter-spaced labels. Older rules still use `--line`, `--cyan`, `--green`… (aliases).

## 2. Components (`style.css` §3)

- **Buttons**: `<button>` = primary · `.secondary` · `.ghost` · `.danger` · `.small` · `.icon-btn` (34px, inline SVG) · `.link-button` · `.chip` (`.on`/`aria-pressed`).
- **Inputs**: plain `input/textarea/select` (styled globally); `label` above; `.field-grid` for rows of fields; `.toolbar` for search + filters; `.form-actions`.
- **Surfaces**: `.panel` (+ `.panel-heading` with `h2` and `.muted`), `.card`; `details>summary` is a disclosure with chevron.
- **Tables**: `table` dense rows; `.table-wrap` scrolls; on phones turn a table into rows with a page rule (see `table.task-table` in §7).
- **Status**: `.pill` / `.badge` + `.st-<status>` (completed, running, human_approval_required, failed, …); `.dot` (+ `.ok/.warn/.bad`), `.st-dot-<status>`.
- **Tabs**: `.seg`/`.tabs` (segmented, `aria-pressed`/`aria-selected`); page tabs live in `#subnav` (see §3); project tabs `.ptabs`.
- **Menu**: `.menu` > `.menu-item` (positioned by its wrapper, e.g. `.profile-wrap`).
- **Dialog**: `uiConfirm / uiPrompt / uiForm` in `app.js` (`dialog.ui-dialog`) — never native dialogs.
- **Drawer**: `.drawer` > `.drawer-backdrop` + `.drawer-panel` (`.drawer-head`, `.drawer-body`) — right side ≥721px, bottom sheet on phones. `.sheet` is the same for the New task form. The task drawer `#detail` is moved to the top level at start-up so it opens over any page (`openTaskDetail(id)`).
- **Feedback**: `.skeleton` / `.skeleton-row`, `.empty-state` (with `strong`), `#notice` (toast; `notice(text)`), `[data-tip]` tooltip, `.typing` dots.
- **Orb**: `.orb` (+ `.orb-xs` 26px, `.orb-sm` 64px; default 160px via `--size`) with `<i class="orb-glow"><i class="orb-ring"><i class="orb-core">`; `data-state` = idle | listening | thinking | speaking | attention | error | offline; `--lvl` (0..1) drives speaking; `.orb-sim` simulates speech. Paused while hidden (`body.anim-paused`), still with reduced motion.

## 3. Shell and navigation (`app.js` HLA_UI_V3, `style.css` §4/§7)

Five sections, defined once in `SECTIONS` (key, label, views). The sidebar (`aside.sidebar`) holds a `.nav-section`
button per section; on phones (≤720px) the same buttons are the bottom tab bar. Between 721 and 1100px the sidebar is
an icon rail (also by the collapse button, remembered in `store('side')`).

| Section | Pages (view id → tab label) |
|---|---|
| Home | `overview` |
| Chief | `chief` (Assistant: voice + text), `threads` (Conversations) |
| Work | `tasks`, `projects`, `queue` |
| Infrastructure | `infrastructure` (Servers: overview, VMs, nodes, network, created servers), `websites` (Sites, Domains & routes) |
| Settings | `agents` (AI & Agents), `models` (Models & Providers, with Subscriptions), `knowledge` (Knowledge & Memory), `mcp` (Tools & MCP), `connections` (Connections & Credentials), `policy` (Security & Permissions, unhinged mode), `costs` (Costs & Usage), `audit` (Audit & Activity) |

How it works: every page keeps its `<section id="<view>">` and its button `button[data-view="<view>"]` (blocks add
theirs to the sidebar as before). At start-up the shell moves each such button into `#subnav` as a `.tab` of its
section (order from `SECTIONS`, label from `TAB_LABELS` or `VIEW_TITLES`). `switchView(view)` shows the page;
the v3 wrapper marks the section (`aria-current`), shows that section's tab group (`#subnav` hidden for a single
page), sets the breadcrumb `#page-crumb` and remembers the page per section (`store('sec.<key>')`). Use
`openView(view)` to navigate from code (clicks the tab, so its load handlers run). `#page-title` = `VIEW_TITLES[view]`
(Home shows the greeting).

Deep links: `#/<section>`, `#/<section>/<view>` and `#<view>`; the older `#chief`, `#websites`, `#inbox`,
`#project=<id>`, `#thread=<id>`, `#task=<id>` keep working. Links are cleared after opening.

**Add a page**: create `<section id="x" hidden>` (in `index.html` or from a block), set `VIEW_TITLES.x='Title'`,
add a nav button `button.nav[data-view="x"]` to the sidebar like the other blocks do (click → `switchView('x')` +
load), and add `'x'` to a section's `views` in `SECTIONS` (else it lands in Settings). It then appears as a tab,
in the palette (Go to) and gets a deep link automatically.

**Add a drawer**: copy the `.drawer` structure, append it to `#workspace`, toggle `hidden`, close on Escape and
backdrop click, focus the first control when opened.

## 4. Home, Chief, Work

- **Home** (`#overview`): status line (`homeStatus()`), Ask Chief (`#cmd-form` → `cmdSend()` → the Chief's thread,
  reply streamed into `#cmd-reply`), quick chips, inline metrics (`.kpis`), "Needs attention" (`#inbox-panel`, only
  when there is something; else `#home-clear`), Recent activity (`renderFeed`, display titles), Infrastructure
  (Proxmox from `/ui/api/proxmox` at most every 2 min, plus the websites tile `#ws-tile`), Projects. New task opens
  `#launch-sheet`. Data: `loadHud()`.
- **Chief** (`#chief`): the car-mode block (`HLA_VOICE_V1`) rearranged into `.chief-stage` (orb `#chief-orb`,
  status), transcript `#car-transcript` (older long replies fold; "Show more"), `.chief-dock` (text `#chief-form`
  sends a typed turn: `carSend(text, true)` — shown, not spoken; tap to talk, hands-free, briefing, stop), settings
  `#car-settings`. "Car mode" (`body.car-focus`) enlarges the controls. With server speech the orb follows the audio
  level (`orbAudio`, only through a running AudioContext). `chiefAsk(text)` opens it from anywhere.
- **Work**: `#tasks` with status chips (`.work-filters`, driving `#task-status-filter`), rows show `display_title`
  (from the API) → drawer `#detail`. Projects: list + detail; the detail is split by `projectTabs()` into
  Overview · Plan · Log · Files (metadata line and addresses stay above the tabs; the tab is kept per project;
  awaiting approval opens on Plan).

## 5. Global

- **Command palette**: Ctrl/⌘ K (`paletteOpen()`); entries from `paletteEntries(q)`: Ask Chief, Go to (every page),
  actions, recent projects / conversations / tasks, agents, servers. Arrow keys + Enter, Escape closes.
- **Shortcuts**: `/` Ask Chief on Home · `g` then `h c w i s` · `?` list.
- **Notifications**: bell `#notif-btn` → `#notif-panel` (approvals from the inbox, infrastructure warnings, failed
  and finished runs of the last 24 h); the device push switch `#push-toggle` is in its header.
- **Unhinged mode**: one calm amber indicator in the header (`#unhinged-banner` with "Turn off"), on every page.

## 6. Inventory (kept from v2; nothing removed)

Pages: overview, chief, threads, tasks, projects, queue, infrastructure, websites, agents, models, knowledge,
connections, mcp, policy, audit, costs (no Monitoring tab: there is no monitoring data in the platform to show). API calls used by the UI: `session login logout summary inbox tasks
tasks/{id} tasks/{id}/events tasks/start-stream tasks/{id}/approval threads threads/{id} threads/{id}/messages
threads/from-task/{id} projects projects/{id}(/approve /replan /pause /resume /cancel /notes /knowledge /tasks/…)
agents models models/sync models/default model-routing costs queue schedules facts facts/recheck policy sandbox
sandbox/reset proxmox(/action /create /preview /snapshots /jobs /level) omada(/level /trust) websites(/summary
/refresh /{id}) routes routes/domains routes/requests routes/check-dns credentials http-tools mcp/servers
deployments attachments chief subscriptions(/{id}/login /login/code /login/cancel /logout /test) voice/config voice/transcribe voice/speak push(/subscribe /unsubscribe /test)`.
Element ids used by `tests/e2e/e2e.js` are all kept (the More sheet `#more-sheet/#nav-more` is gone: its job is done
by the sections and tabs).

## 7. Phase 4 pages (HLA_UI_V4)

Same foundation, plus the shared pieces in §8. Every page starts with `.page-intro` (one sentence of what it is, its
one or two actions on the right), then a summary (`.metric-strip`), then the content; details open in a drawer.

| Page | Summary first | Content | Drawer |
|---|---|---|---|
| Infrastructure › Servers (`#infrastructure`) | `#infra-overview`: nodes healthy, VMs running, CPU, memory, websites; `#infra-alerts` (unreachable node, missing token rights, failed create, website down, Omada certificate) | tabs `#infra-tabs`: Virtual machines (`.px-table`: Name, State, CPU, RAM, Host; `#px-search`), Nodes (`.px-node` rows), Network (Omada `#om-body`), Created servers (`.px-server` with level) | `#px-drawer`: a VM (facts, agent access and its server level, snapshots, **Actions** menu `.px-actions-btn` with power, snapshot, roll back, onboard, destroy) or a node (agents' level, Resources, token rights, **Create VM**, More › read-only token). Every action keeps its confirmation and the gateway's checks (`pxAction`). |
| Infrastructure › Websites (`#websites`) | `.ws-totals` (Up, Up · protected, Degraded, Down) and environment chips | tabs `#ws-tabs`: Sites (`.ws-table`: Website, Environment, Status, Response, Checked; grouped by environment, `#ws-filter`, `#ws-search`) and Domains & routes (`#rt-panel`, count of waiting requests `#ws-routes-count`) | `#ws-drawer`: addresses, details (environment, certificate, up for, compose folder, notes), containers, Traefik routes, **Edit** (`.ws-edit`), Open site. `openWebsite(id)` opens it. |
| Settings › Knowledge (`#knowledge`) | counts on the tabs | tabs `#kb-tabs`: Notes (`#kb-list` rows: title, one-line description, category, updated; live search) and Recorded facts (`#fact-list`) | `#kb-drawer`: the note (markdown), Edit (the `#kb-form`, parked in `#kb-form-home` when closed), More › Open project / Delete. **New note** opens the form. |
| Settings › Agents (`#agents`) | `#agent-summary` | `#agent-list` rows | `#agent-drawer` with `#agent-form` (parked in `#agent-form-home`); New agent; More › Delete |
| Settings › Security (`#policy`) | — | Permissions (matrix stacks on phones) first, then Unhinged mode (calm amber control), Sandbox, Deployments | — |
| Settings › Costs (`#costs`) | `#cost-totals`, chart, project budgets | `#cost-breakdown` (details): by kind, model, project, agent, top runs, daily figures | task drawer from a top run |
| Settings › Audit (`#audit`) | `#audit-summary` (latest entries, commands, refused, failed, approvals; Refused filters) | filters + the full log | `#audit-detail` |
| Work › Projects › Plan | — | compact task cards (`.ptask`: title, two lines of instructions, done when, **Edit**) | `#ptask-drawer` with the task's fields; they live in the card (hidden) while closed, so unsaved edits are still saved on approval and survive re-renders |

Settings tabs carry short names; the page title names the group: AI & Agents, Models & Providers, Knowledge & Memory,
Tools & MCP, Connections & Credentials, Security & Permissions, Costs & Usage, Audit & Activity (`TAB_LABELS`,
`VIEW_TITLES` in the v3 shell block). The approver status lives in the profile (`#profile-sub`, `#approver-badge`
in `#profile-menu`; on phones the header avatar `#profile-mobile` opens `#profile-sheet` with Log out
`#logout-mobile`) and on every approval card (`.approver-line`).

## 8. Shared Phase-4 pieces (`app.js` HLA_UI_V4 block after the dialogs; `style.css` §8)

- `drawer(id, wide)` → `{title, meta, actions, body, open(), close(), isOpen, onclose}`; created once, appended to
  `#workspace`; right side ≥ 721 px, bottom sheet on phones; Escape and the backdrop close it (not while a dialog is
  open); the page behind is inert; focus returns to what opened it. Re-render a drawer by calling its open function
  again with fresh data (pages do this after a refresh while it is open).
- `actionMenu(label, items, cls)` → a button with a menu (`.menu-wrap`): items `{label, run, danger, disabled, title}`,
  `null` for a separator; arrow keys, Escape and an outside click close it; `run` errors become a notice.
- `metric(label, value, foot, {tone:'warn'|'bad', bar:0..1, onclick, id})` → a tile of a `.metric-strip`.
- `factList([[label, value]])` → `<dl class="facts kv">` (empty values skipped).
- `paneTabs(tabsEl, scope, onchange)` → wires `.tabs` buttons (`data-tab`) to `[data-pane]` blocks; returns `show(key)`.
- CSS: `.page-intro`, `.page-toolbar`, `.pane-intro`, `.alert-list/.alert-row`, `.row-list/.row-item` (`.row-title`,
  `.row-sub`, `.row-side`), `.drawer-section`, `.tab-count` (attention) and `.tab-n` (plain count), `a.button-link`.

## 9. Review fixes (HLA_UI_V5; `app.js` V5 block at the end, `style.css` §9)

- **Icons**: one SVG set, `ICONS` + `ico(name)` / `withIcon(button, name, label)`; no emoji or unicode glyph icons.
- **One status source**: `platformStatus()` feeds the sidebar status, Home's status line and the bell; unhinged mode shows
  as "Unhinged on · until …" in the sidebar and as the header pill (sticky header on wide screens, a slim fixed strip on phones).
- **One primary per view**: a plain `<button>` is primary; row/list actions, "+ New" next to a real primary and secondary
  forms are demoted in §9; toggles (Preview/Raw, OpenRouter/xAI) are segmented controls.
- **Work**: status chips are the only status filter; repeated runs fold to "×N"; runs show `display_context`.
  Queue: rows → `#queue-drawer`, the form behind "Queue task".
- **Chief**: centred orb + status; Stop only while listening/speaking/hands-free; voice settings in `drawer('voice-drawer')`;
  Car mode (`body.car-focus[data-view=chief]`) hides the shell, `#car-focus-exit` leaves it; replies render as Markdown.
- **Settings**: MCP servers are rows (`.mcp-row`) → `#mcp-drawer`; the GitHub allow-list lives on Connections; long
  intros clamp to one line with "More" (`clampIntros`).
- **Notifications**: grouped (Needs you · Alerts · Runs) with times, read state and Clear (localStorage, guarded).
- **Accessibility**: `--text-2`/`--text-3` meet 4.5:1 on every surface; `#skip-link` is the first Tab stop.

## 10. Subscriptions (HLA_SUBSCRIPTIONS_V1; `app.js` block after V5, `style.css` §10)

Claude (Pro/Max) and ChatGPT (Plus/Pro) plans run models through the vendors' CLIs in the router (`/ui/api/subscriptions`,
`…/{id}/login`, `login/code`, `login/cancel`, `logout`, `test`; writes need an approver, a 403 becomes a notice).

- **Where**: Settings › Models (`#subs-panel`, first panel): it is a way to run models, next to the registry it feeds.
  Connections holds gateway credentials for tools; a subscription sign-in lives in the router and is not a credential.
- **Rows** (`.subs-row[data-engine]`): name, status pill (Not installed · Signed out · Signing in… · Connected), account ·
  plan, a muted hint and version, `last_error` in red, the Test result (`.model-test`). Actions: **Sign in** (the page's
  one primary, first signed-out engine only; others secondary), **Test** (connected), **More** › Cancel sign-in / Sign out
  (danger, `uiConfirm`).
- **Sign-in drawer** `drawer('subs-drawer')`: numbered steps — "Open sign-in page" (`a#subs-open`, https only, new tab,
  `noopener`), the device code `#subs-user-code` large with Copy, or "Paste the code" `#subs-code-input` + Submit when
  `needs_code`; polls every 2 s while starting/waiting and re-renders only on change (a typed code survives); ends in
  Connected (Done) or the error (Try again); Cancel calls `login/cancel`.
- **Pickers**: `subsDecorate(select)` (after `loadAgents`, `loadRoutingModels`, `renderRouting`, `renderProjectRouting`,
  `renderModels`, `carOpen`) moves models with `-sub/` ids or `billing:"subscription"` into an `optgroup` "Subscriptions";
  a signed-out engine's models are disabled with "(sign in first)" (missing ones are added from the engine's list); a
  violet `.plan-pill` "Plan" follows the select while one is chosen. Signed-in engines' models need no registry row (`/ui/api/models` lists them in `models`), so the Default model picker offers them too. Project planners pick an agent, not a model.
- **Costs**: `isSubModel(id, billing)`; runs, the task drawer and Costs › By model read "Subscription", never $0.00;
  registry rows show provider "Subscription" and price "plan".
