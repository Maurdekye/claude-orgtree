<!-- ⚠ IMPLEMENTATION HELD by direct user instruction (2026-08-01,
     reaffirmed "wait for now" 2026-08-14): do not build this wave until the
     user gives the go-ahead. The spec is stored build-ready. §0 (safety)
     and the two desktop bugs it names were fixed independently — they were
     live defects, not mobile work. The design ruling this spec needed is
     now GIVEN: the compact-screen desk sheet is approved (D-123,
     2026-08-14) — no open questions remain, only the go-ahead. Spec
     author: the read-only secondary session, from a 6-surface audit of the
     strict-TS frontend.
     2026-08-14 (later, "prepare the mobile wave"): DRIFT AUDIT APPENDED as
     §9-§11 — the §0-§8 analysis substantially HOLDS at HEAD, but ~5k lines
     of frontend growth added surfaces this spec never saw (watchdogs, the
     ask system, edge-gated hire chips, drag-only granting). §11's reopened
     decisions were RULED the same day (D-125): coarse-pointer sheet gate,
     watchdogs off the compact map, hire-form placement; tap-granting and
     the orgbar absorption NOT taken. Scope ruling: spec-refresh only — the
     wave (including §8 steps 1-2) remains HELD until the go-ahead. -->

# orgtree — mobile responsiveness: build-ready spec

Author: session 4f69f83a · 2026-08-01 · drafted from a 6-surface source audit
(177 findings, 52 blockers) plus hard-parts and breakpoint analysis. Every number
below is measured from the source at the cited line, not estimated.

Status: **spec only.** The user placed the wave on implementation hold — this sits
ready for their go-ahead. Nothing here should be built yet.

---

## 0. Read this first — the ordering hazard

**Relaxing `touch-action` before fixing `pointercancel` semantics will corrupt users' orgs.**

`.viewport` carries `touch-action: none` (styles.css:111) — the only touch declaration in
1,732 lines of CSS. It is why nothing inside the canvas scrolls on a phone. The obvious first
move is to relax it. Do not.

`touch-action: none` currently suppresses the browser's scroll-vs-drag disambiguation. Relax it
and the UA starts arbitrating gestures — and when it decides a gesture was a scroll, it fires
**`pointercancel`**. `onPointerCancel` on a card is wired straight to `endNodeDrag`
(cards.tsx:672), whose no-drop branch issues an unconditional `reorderNode()` POST
(OrgCanvas.tsx:707-747). So a browser-initiated gesture cancellation becomes a live org
restructure.

That is a one-line CSS change that silently mutates the user's tree, on their phone, with a
toast as the only recourse. **`pointercancel` must abort without committing before any CSS in
this spec is touched.** It is item 1 of §3 for that reason and no other.

---

## 1. The verdict, and the one decision that needs ratifying

**The spatial canvas does not survive at 375 px as a work surface. It survives as a locator.**

This is arithmetic, not taste. The desk is authored at 900×900 px and counter-scaled by
`0.13333` into the 124 px card (styles.css:370-374). On-screen text size is
`authored × 0.13333 × view.z`. The stylesheet states its own intended readable point:
*"at desk-fill zoom (z ≈ 7.5) authored px ≈ screen px"* (styles.css:365-369) — which puts the
card at 930 screen px. Meanwhile `centerOn` picks focus zoom as
`(min(vp.width, vp.height) − 48) / 124` (OrgCanvas.tsx:521-523), so **portrait is governed by
width**:

| viewport | focus z | `.msg` 14 px renders at |
|---|---|---|
| 375×667 phone | 2.49 | **4.4 px** |
| 390×844 phone | 2.55 | 4.6 px |
| 844×390 landscape | 1.85 | **2.7 px** |
| 768×1024 iPad portrait | 5.52 | 10.3 px |
| 1920×1080 desktop | 8.32 | 13.6 px |

No zoom satisfies both legibility and framing, because they are the same variable. Every mobile
browser floors font size around 10-12 px, so the primary work surface of the application is
unreadable at every zoom that fits a phone.

**∴ On compact screens the desk stops being a world-scaled object and becomes a full-screen
sheet at 1:1.** The canvas remains — as the map you navigate *from*, not the surface you work
*in*.

⚑ **This is a product decision, not an implementation detail, and it contradicts a written design
ruling.** The desk "fades in over the card at the same size" is deliberate (styles.css:350-356,
cards.tsx:655-656) — the chat living *inside* the tree is the idea the whole card metaphor
exists to deliver. This spec preserves that on desktop and abandons it on phones. **State it in
DECISIONS.md before building, or an implementer will read the sheet as a bug and revert it.**

Two existing cliffs found on the way, unrelated to mobile and worth fixing regardless:
- Below ~346 CSS px window width, `centerOn` cannot reach `Z_DESK = 2.1` at all — tapping a card
  animates the camera and **no desk ever renders**, at any zoom, with no error.
- The HUD's eye button computes `(vp.height − 48)/124` while the switchboard gate is
  `0.85 × zFill` (OrgCanvas.tsx:768-771). In landscape, **pressing "jump to the switchboard"
  animates the camera below the threshold that opens it.**

---

## 2. Hard parts — where the obvious fix fails

Ordered by how much time the discovery costs. Each states the constraint any real solution must
satisfy.

### ① `focusId` is not state — it is a continuous function of the camera
`focusId = useMemo(…, [view, target, hidden])` returns *the nearest card to the viewport centre*
gated on `view.z ≥ Z_DESK` (OrgCanvas.tsx:751-774). There is no `selectedId` in the codebase.

"Tap a card → open its desk as a sheet" has nothing to set. Adding `mobileFocusId` creates two
sources of truth, and the camera-derived one keeps firing: a **spring-follow** at
OrgCanvas.tsx:452-468 translates the camera by the focused node's per-frame spring delta on every
rAF tick (it exists because a hire used to slide the desk ~1000 px out of the window). Any hire
re-anchors the layout → springs move → camera moves → `focusId` re-evaluates → **the sheet closes
itself**. Five other `centerOn` callers move the camera under you, including the desk's own
background click (desk.tsx:599-606) — so every failed scroll swipe re-zooms the camera.

**Constraint.** One source of truth. Either promote focus to explicit state and teach the
spring-follow plus all six `centerOn` sites to respect it, or keep it camera-derived and *freeze
the camera* while a sheet is open — a third guard alongside `panRef`/`animBusyRef` that the
follow at :453 also reads. Anything else and the sheet is haunted.

### ② `touch-action` narrows going down, and every scroller is nested inside the pan surface
Effective `touch-action` is `parent ∩ own`; a descendant cannot re-widen. `none ∩ pan-y = none`.
`.msgs`, `.eye-panels`, `.mailer-*`, `.settings`, `.thoughtbody`, `.respre` and six canvas-hosted
modals are all descendants of `.viewport`.

So `touch-action: pan-y` on `.msgs` does nothing, and relaxing `.viewport` triggers §0.

**Constraint.** The pan surface *contains* the scroll surfaces and CSS cannot express that
nesting in the direction needed. Separate them physically: portal the desk and all six modals to
`document.body`. The pattern is already proven in-repo — `DraftScopeModal` portals for the exactly
analogous transform reason (modals.tsx:244, 328). The wheel carve-out
`e.target.closest('.desk-over')` (OrgCanvas.tsx:590-591) survives a portal fine: a portaled desk
is no longer a DOM descendant, so the native listener never sees it.

### ③ The counter-scale factor is one equation with zero slack, duplicated across CSS and TS
`styles.css:371-372` (`width: 900px; transform: scale(0.13333)`) and `cards.tsx:223`
(`innerW = round((eyeW − 4) / 0.13333)`). The identity is `900 × 0.13333 = 120 = NODE_H(124) −
2×inset(2)`. Three numbers, one equation, only `NODE_H` exported. No CSS edit *inside* the desk
helps — everything inside is multiplied by the same factor.

**Constraint.** The desk must stop being world-scaled. The cheap route is already built:
`--invz = min(2.4, max(1/12, 1/view.z))` (OrgCanvas.tsx:876) — over the desk's entire range
`z ∈ [2.1, 12]` **neither clamp bites**, so `--invz ≡ 1/z` exactly, and
`scale(calc(0.13333 * var(--invz) * k))` gives authored px = screen px at every desk zoom with no
JS. It also crosses `DeskChat`'s memo boundary for free (desk.tsx:91-95 compares data props
only), where a `zoom` prop would force a full transcript re-render every frame.
⚠ But a screen-constant desk exceeds its card and `.desk-over { overflow: hidden }`
(styles.css:360) clips it — **so ③ collapses into ① unless the desk leaves the card box.**

### ④ "Just un-hide the hire chips" is geometrically impossible below z ≈ 0.54
`.hsof` is 4×22 px + 3×4 px gap = 100 screen px, counter-scaled to stay constant. Column pitch is
`SX = 186` **world** px (shared.ts:151) = 186·z screen px. Chips of adjacent cards overlap
whenever `100 > 186z` ⇒ **z < 0.538**. At a 44 px touch target: `4×44 + 3×8 = 200` ⇒ collision
below **z = 1.075**, which is above `Z_MINI` and above almost every `fitAll` output.

Worse than visual: `.sq` carries an inline transform, so **each card is its own stacking
context** — `.hsof { z-index: 3 }` is scoped inside the card and inter-card overlap resolves by
DOM order. An always-on chip row becomes a `pointer-events: auto` tap-thief over the neighbouring
card.

**Constraint.** The hire affordance cannot be "the same chips, always on". It needs a gate
guaranteeing *exclusivity* rather than hover. That gate exists — `.sq.desk > .hsof`
(styles.css:805) shows them ungated for the one focused card — so the hire fix is downstream of
①. Or move hiring to screen space, where world pitch is irrelevant.

### ⑤ Raising the drag threshold does not restore panning
`|Δx| + |Δy| > 5/z` is a flat **5 screen px** Manhattan gate (OrgCanvas.tsx:692); finger jitter
on a tap is 8-15 px. But the deeper problem is earlier: `startNodeDrag` calls
`e.stopPropagation()` **unconditionally at pointerdown** (:659), before any threshold is
evaluated. A finger that lands on a card can never pan — at any distance, at any threshold. At
zoom levels where cards cover the screen, panning has no surface at all.

**Constraint.** Arbitration moves to pointerdown: record the point, take **no capture and no
`stopPropagation`** until intent resolves (hold ⇒ drag, move ⇒ pan, release ⇒ tap), then commit
to one. Note two competing `setPointerCapture` calls exist for the same pointerId on different
elements (viewport :619, card :668) — last call wins, so hand-off order is load-bearing.

### ⑥ A pinch handler cannot be bolted onto the existing pan handlers
`OrgCanvas.tsx:614-628` has one `panRef`, no pointerId map, and `onPointerUp` nulls
unconditionally. Three failure modes: (a) **the second finger usually lands on a card, and cards
`stopPropagation()`** — React's bubbling handlers never see pointer 2, so pinch must be captured
natively or in the capture phase, exactly as the wheel already is and for the reason documented
at :588-589; (b) `viewRef.current = view` is assigned **during render** (:211) and both wheel and
`zoomStep` write absolute `setView` computed from it — at 120 Hz several pointermoves land per
commit, each reading a stale ref, last write wins ⇒ drift; the pinch path must write
`viewRef.current` synchronously; (c) the spring-follow yields only to `panRef`/`animBusyRef`
(:453), so a pinch registering in neither is fought every frame — precisely when a desk is open.

**Constraint.** Native/capture-phase pointer bookkeeping, synchronous `viewRef` coherence,
participation in the existing exclusion vocabulary, and a second pointer must **abort** any
in-flight node drag without committing (§0).

### ⑦ Nothing re-runs on resize, and the loop that would paper over it goes idle
Zero `resize`/`ResizeObserver`/`visualViewport`/`matchMedia` listeners exist. `focusId` (:751),
`eyeW` (:967) and `lod` (:777) derive **during render**, and the rAF tick only calls `setFrame`
when `active || nodeDrag.moved` (:475) — so once springs settle, OrgCanvas stops re-rendering
entirely. On rotate there is no re-render at all: `eyeW` keeps the old aspect, `focusId` tests
against the old centre, and `view.x/y` — absolute offsets against the old rect — are wrong.

A ResizeObserver must therefore also **re-run the camera** (`fitAll`, or re-`centerOn(focusId)`),
not merely force a render.

**Soft keyboard.** `main { height: 100vh }` does not change when the keyboard opens, so
`.viewport`'s rect doesn't either — `centerOn`'s fill-the-window math targets a window that is
~40% occluded. `visualViewport` is the only API reporting this. The browser's own
scroll-into-view remedy is **deliberately cancelled twice** (:442-446, :864-871) for a still-valid
reason. **Constraint: keyboard accommodation must move the camera, never the scroll position.**

### ⑧ Two obvious perf fixes each break something
`memo()`-ing `NodeSquare` looks like the big win and will **silently freeze every card**: `posOf`
(:232) returns the spring object itself, mutated in place at :427 and :696 — referentially stable
while its contents change, so a shallow comparator sees nothing and cards stop moving during
drags and springs. Pass `pos.x`/`pos.y` as scalars before memoizing. *This one ships, looks fine
in a screenshot, and costs half a day.* Separately, idling the rAF is correct but the loop is also
the frame-by-frame self-heal for native scroll (:442-446); the `onScroll` handler (:864-871)
covers the same case, so it is probably safe — verify, and note the hazard moves with the desk if
it gets portaled.

### Cross-system interactions
- **② → ⑤ (sharpest, see §0).** Fix cancel semantics before touching touch-action CSS.
- **③ → ①.** Screen-constant desk exceeds its card; escaping the card means escaping world space
  means focus needs real state.
- **④ → ①.** The only non-hover chip gate is defined in terms of desk focus.
- **① → ⑥.** Camera-freeze and pinch both register in the same `panRef`/`animBusyRef` vocabulary
  the spring-follow reads. **Build one guard, not three.**
- **② → ③ (a freebie).** Portaling the desk also un-traps `ConfirmModal` (desk.tsx:418-433),
  whose `position: fixed` overlay is currently contained by `.desk-inner`'s transform and renders
  at `0.13333 × z`. The irreversible-dissolve dialog becomes real-sized as a side effect.

---

## 3. Prerequisites — land before any breakpoint work

These are tier-independent. Several are live bugs on desktop too.

1. **`pointercancel` aborts, never commits** (cards.tsx:672). §0. Do this first.
2. **6 px movement threshold + `pointerType` gate on `CreditBar.start`** — currently zero
   threshold, so any tap that drifts commits a live `reallocate`.
3. **Armed-delete disarm** — `onMouseLeave` never fires on touch, so a multi-GB delete degrades
   to a single tap. Replace with a 3 s timeout + outside-`pointerdown`.
4. **`opacity: 0` → `visibility: hidden`** on `.gearbtn`/`.mailbtn`/`.hsof`/`.org-del` —
   invisible-but-hit-testable targets; ⚙ opens a permissions panel from a blind corner tap.
5. **Portal all six canvas-hosted modals + the tray to `document.body`** (②).
6. **`vh` → `dvh` at all 7 sites** (`100vh` ×3, `88vh`, `84vh`, `55vh`, `46vh` ×2, `44vh`) —
   `vh` resolves to the *large* viewport, so the HUD and every panel footer sit under browser
   chrome at first paint.
7. **`viewport-fit=cover` + `interactive-widget=resizes-content`** in index.html:5, and
   `env(safe-area-inset-*)` on bottom-anchored chrome — nothing clears the home indicator today.
8. **`.lineage-panel { min-width: 480px }` → `min(480px, 100%)`** — `min-width` beats
   `max-width`, so rehire/retire buttons are clipped off-screen with no scroll.
9. **`-webkit-text-size-adjust: 100%`, `-webkit-tap-highlight-color: transparent`,
   `overscroll-behavior: contain`** on sheets — grey flash on every card tap; pull-to-refresh
   mid-pan.
10. **ResizeObserver + `visualViewport` that re-run the camera** (⑦).

---

## 4. Breakpoints — three axes, not one ladder

A width ladder gets landscape phones wrong, so the system is three orthogonal predicates.

**Axis A — width tier (screen-space layout only).**
`compact ≤ 640` · `medium 641–1023` · `full ≥ 1024`.
640 because three independent derivations converge in the 600-640 band; 1024 because it is where
4-5 legible columns fit (`3·186+124 = 682 × 1.16 + 48 = 839`) and where a docked desk rail still
leaves ≥600 px of canvas.

**Axis B — desk host predicate (not a width breakpoint).**
`deskHost = min(vpW, vpH) ≥ 780 ? 'card' : 'sheet'`.
780 derives from the stylesheet's own stated floor (styles.css:356: *"≥10 px fonts only"*):
`.msg` is 14 px authored, so `14 × 0.13333 × z ≥ 10` needs `z ≥ 5.36`, needs
`min(vp) ≥ 5.36 × 124 + 48 ≈ 713`; 780 adds margin for the 13 px chrome font.
⚑ **1600×900 lands at min=772 — 8 px under the predicate.** Decide deliberately whether that
rounds in; it is the most common desktop resolution and it should not flip on a browser-chrome
pixel.

**Axis C — input modality.** `(pointer: coarse)` / `(hover: none)`, orthogonal to size. Governs
hit-target sizing, hover-gating, and whether drag affordances exist at all.

**Plus a short-viewport rule:** `vpH < 500` collapses the orgbar and forces full-bleed panels
regardless of width — this is the landscape-phone case a width-only ladder mishandles.

☞ **The single most important technical ruling: media queries for screen-space chrome; container
queries for anything inside a counter-scaled transform.** A `@media (max-width: 640px)` rule
inside `.desk-inner` is *lying* — that panel is 900 px wide in its own coordinate space no matter
what the window is.

---

## 5. Per-surface adaptation

`ADAPT` = same surface, new layout · `REPLACE` = different surface, same capability · `HIDE` =
capability removed at this tier.

### 5.1 Canvas
| surface | ≤640 compact | 641–1023 | ≥1024 |
|---|---|---|---|
| Pan/zoom input | ADAPT — pinch (2-pointer, anchored), momentum pan, double-tap = zoom-to-card; wheel retained | same | same |
| Zoom range | ADAPT — `[0.36, 1.6]`, `Z_DESK` retired | `[0.30, 12]` | `[0.24, 12]` |
| Card LOD | REPLACE — a *map* LOD: world-scaled tier block + counter-scaled 12 px caption (name, 2 lines) + screen-constant 10 px status dot | unchanged | unchanged |
| Tap a card | ADAPT — **no zoom change**; 200 ms centering pan at constant z, then the sheet opens. Kills the 10× dive, the glide-cancel trap, and the no-way-back-out state | ADAPT | unchanged |
| Drag to reparent/reorder | **HIDE** (§6) | ADAPT — 350 ms long-press arm, lifted state, screen-constant drop ring; edge-pan 48→24 px and `dt`-normalized (currently px *per frame*) | unchanged |
| Credit bar | REPLACE — read-only gauge; `grant · alloc · free · seat` become text in the caption and roster row; reallocation is a stepper + confirm | ADAPT | unchanged |
| HSOF hire chips | REPLACE — hire is a full-screen form from the roster header and card overflow; four tiers become four 56 px rows | ADAPT | unchanged |
| Per-card ⚙ / ✉ | HIDE — moved to roster-row overflow and the sheet header | ADAPT — `visibility`, 44 px, 12 px separation | unchanged |
| Piles | ADAPT — one card + screen-constant 20 px count badge → PilePicker bottom sheet; phantom layers hidden | ADAPT — fan out on `:focus-within` too | unchanged |
| Switchboard (EyeDesk) | HIDE — replaced by a roster filter "direct lines" | HIDE unless landscape | shown iff `vpW > vpH ∧ min ≥ 780` |
| Eye card | HIDE — "you" and org inbox move to the orgbar ⋯ menu. Bonus: dropping `EYE_ANCHOR_X = 6000` from bounds shrinks the painted `.space` layer from ≥6560 px to the tree extent | shown | shown |
| `fitAll` | ADAPT — fit **height** to tree depth, `z ∈ [0.36, 0.8]`, horizontal overflow shown by edge chevrons (orgs grow horizontally; portrait screens don't) | ADAPT | unchanged |
| Zoom HUD | REPLACE — one 56 px "fit" FAB at `bottom: calc(12px + env(safe-area-inset-bottom))`; ± removed (pinch and double-tap replace them, and a 4 px-apart ± pair is a guaranteed mis-tap) | ADAPT — 44 px | unchanged |
| Agents tray | REPLACE — becomes primary navigation (§5.3) | ADAPT — 44 px rows, portaled | unchanged |
| `.space` dot grid | HIDE — a 28 px repeating radial gradient over a multi-thousand-px layer is the worst paint on a mobile GPU and conveys nothing | shown | shown |
| Spring/rAF | ADAPT — 30 Hz, no elastic overshoot, cull cards+edges outside `rect + 1 screen` | idle when settled | idle when settled |

### 5.2 The desk sheet
When `deskHost === 'sheet'`: full-screen, portaled to body, authored 1:1 — no counter-scale, no
world transform. Header carries the node identity, tier, ⚙, ✉ and close. Transcript scrolls
natively (`touch-action: pan-y`, `overscroll-behavior: contain`). Composer docks to the bottom
with safe-area inset and follows `visualViewport` on keyboard open. Back gesture / hardware back
closes the sheet before leaving the org.

### 5.3 Compact information architecture
**The tray becomes primary navigation below 640 px.** It is already 80% of a roster
(OrgCanvas.tsx:1100-1185): tier chip, name, status, filter. Promote it to a persistent
bottom-sheet peek; the canvas becomes the *map* you open from it, not the thing you navigate by.

### 5.4 Screen-space panels
Every `.overlay` becomes a full-bleed sheet at compact: `inset: 0`, radius 0, 44 px close,
`padding: 16px`, **`position: sticky` footer** + safe-area (fixes save-below-the-fold). `.mailer`
two-pane → single pane with list→read push navigation and a back chevron. Org drawer gains
`overflow-y: auto` — **it is not a scroll container today, so the NewOrg submit row is
unreachable**. DiskBrowser rows become 56 px two-line (name middle-ellipsis + size, dim parent
path) and **the full path must leave `title=`** — this is the emergency surface used when the
disk is hard-full and a phone has no hover. Disk-full banner wraps, is dismissible, and sets
`--banner-h` offsetting `main`'s padding (today it buries the hamburger). Orgbar collapses from
12-14 wrapping chips to one 44 px row: ☰ · org name · one merged status chip · ⋯ overflow.
`main` padding `18px → 0` at compact — recovers 36 px, 10% of a 375 px screen, and raises
`min(vpW,vpH)` from 339 to 375.

---

## 6. Does not adapt — desktop-only by design

| surface | why not |
|---|---|
| In-card counter-scaled desk | Its whole value is the chat living *inside* the tree. Shrunk, it is neither. Replaced, never adapted. |
| Switchboard / EyeDesk multi-panel | N-up parallel conversations is an area-bound idea; one column of it *is* the desk. A one-panel switchboard is a lie. |
| Drag to reparent / reorder | Needs a hover preview, a drop target not occluded by the input device, and a cheap escape. Touch gives none of the three. |
| Drag to reallocate credit | Precision is `pxc × z` ≈ **2.0 px per credit** at z=1. A finger cannot resolve one credit, and there is no stationary tap. |
| Hover tooltips as a data channel | Tap-to-reveal creates a mode where a tap sometimes reveals and sometimes acts. The *data* is promoted to text; the *mechanism* stays desktop. |
| All 112 `title=` attributes | Kept — free accessibility, zero cost. The rule is not "remove titles", it is **"no fact exists only in a title"**. |
| Edge-pan during node drag | 48 px bands are 27% of a 375 px width. There is no correct tuning, only a correct absence. |
| Enter-to-send / Shift+Enter | Soft keyboards emit `Enter` with `shiftKey:false` and there is no gesture to recover the newline. Disabled on coarse, not adapted. |
| Long-press context menus | Explicitly not added — zero `onContextMenu` handlers exist, so nothing is lost, and a long-press menu would fire during slow pans. |
| Lineage shadows, pile phantom layers, `.sq:hover` z-promotion, `resize:` grips, the persistent 264 px aside | Decorative or desktop-mechanism. The count badge carries the information. |

---

## 7. Verification matrix

| device | tier | deskHost | expected |
|---|---|---|---|
| 375×667 | compact | sheet | map @ 0.36 floor, roster peek, desk sheet, orgbar 1 row |
| 390×844 / 430×932 | compact | sheet | 5 columns tappable at 44 px |
| **844×390 landscape** | medium **+ short** | sheet | orgbar collapsed, full-bleed modals, canvas primary — *the case a width-only ladder gets wrong* |
| 673×841 foldable | medium | sheet | canvas primary |
| 768×1024 iPad portrait | medium | sheet (min 732) | switchboard hidden (portrait) |
| 1024×768 iPad landscape | full | sheet (min ~600) | switchboard hidden by `min < 780` |
| 1600×900 | full | **card (min 772) — marginal** | ⚑ 8 px under the predicate; rule on it deliberately |
| 1920×1080 | full | card | unchanged desktop |

---

## 8. Build order

1. **Safety** — §3 items 1-3 (`pointercancel`, credit-bar threshold, armed-delete disarm). These
   are live bugs; they ship independently of any mobile work.
2. **Structural** — portal modals + desk out of `.viewport` (②), then touch-action can be relaxed
   safely.
3. **Focus** — one source of truth (①) plus the single shared guard that ⑥ and the camera-freeze
   both register in.
4. **Input** — pointerdown arbitration (⑤), pinch (⑥), ResizeObserver + visualViewport (⑦).
5. **Layout** — the three axes, then §5 surface by surface.
6. **Polish** — perf (⑧, carefully), dot-grid removal, culling.

Steps 1-2 are worth landing even if the wave stops there: they fix real desktop bugs and remove
the corruption hazard.

---

## 9. Drift audit — 2026-08-14, at HEAD past `ddb66fa`

Two independent re-surveys ("prepare the mobile wave"): a claim-by-claim verification of §0-§8
against current source, and an enumeration of every surface added in the 91 frontend commits
(~5.1k added lines) since this spec's audit baseline (`a126421`). Line numbers below are current;
the §0-§8 cites are stale (styles.css 1,732 → 2,247 lines, OrgCanvas.tsx → 1,578) but their
*claims* were re-verified individually.

### 9.1 What was fixed since (do not re-do)

- **§0 is cleared for node drags.** `abortNodeDrag` (OrgCanvas.tsx:852) restores bases and posts
  nothing; cards route `onPointerCancel` to it (cards.tsx:780). Landed 2026-08-01 (`a126421`)
  with the two §1 cliffs (sub-346px no-desk; landscape switchboard gate — `centerOn` now floors
  at `Z_DESK`).
- **§3 item 1 done.** Items 2-10 remain open (verified: CreditBar threshold still absent, both
  armed-delete sites still `onMouseLeave`-disarmed at App.tsx:1392 + DiskBrowser.tsx:217, all
  four `opacity: 0` controls intact, index.html meta unchanged, still zero
  resize/visualViewport/matchMedia listeners, still only one `@media` — `prefers-reduced-motion`).
- **§2-③'s TS-side duplication is half-fixed**: `DESK_SCALE = 0.13333` is now the one TS
  definition (shared.ts:238), but the CSS literal survives at styles.css:447, so the CSS↔TS split
  stands.
- **A second §0 instance was found and fixed in THIS audit**: `CreditBar` routed `onPointerCancel`
  into its commit path (`end` fires `onCommit(v - grant)`) — a UA-cancelled gesture committed a
  live reallocation. Now aborts via a dedicated `cancel` (cards.tsx, beside `end`), restoring the
  pre-drag grant.

### 9.2 What changed against the spec

| §0-§8 claim | status at HEAD |
|---|---|
| `.viewport` `touch-action: none`, the only touch declaration | HOLDS (styles.css:165) |
| `focusId` camera-derived, no `selectedId` anywhere | HOLDS (OrgCanvas.tsx:948-971) |
| spring-follow yields only to `panRef`/`animBusyRef` | HOLDS (:575-591) — the "third guard" is still unbuilt |
| counter-scale identity | CHANGED: `--desk-dpi` now divides width and multiplies scale (styles.css:444-448), product invariant; `deskDpi()` (shared.ts:241) is a user text-size dial 0.5-3.0. The equation gained a variable, not slack. |
| `stopPropagation` unconditional at card pointerdown | CHANGED: an interactive-widget carve-out precedes it (OrgCanvas.tsx:805) — but no threshold, capture still immediate (:820). The ⑤ constraint stands. |
| two competing `setPointerCapture` | CHANGED: **three** (viewport :759, card :820, CreditBar cards.tsx:409) |
| canvas-hosted modals to portal | CHANGED: **8** (spec's six + `WatchdogPanel` :1544, `OrgInboxModal` :1567) plus tray (:1412) and zoom HUD (:1394); `ConfirmModal` non-portaled at 10 nested sites; `ComposeModal` is a **modal nested inside a modal** (mail.tsx:661 inside OrgInboxModal). ⚠ These overlays are `position: fixed` so they *render* outside the world transform — but they are DOM descendants of `.viewport`, so `touch-action: none` still kills their scrollers on touch. Portal need unchanged. |
| `vh` sites | CHANGED: 10 (the 9 the spec's list actually enumerated + `.doc-reader { max-height: 86vh }` styles.css:804) |
| hover-revealed `opacity: 0` controls | GREW: `.user-label` (:1134) and `.cbar-tip` (:1284) added; `.hsof`/`.cbar-tip` carry `pointer-events: none` (not blind targets), `.org-del`/`.gearbtn`/`.mailbtn` still are |
| zoom clamp | `0.24` is a magic literal at two sites (OrgCanvas.tsx:630, :737); no `Z_MIN` const. `SX/SY/PAD` are module-private (shared.ts:217). |
| `title=` census | 112 → **140** |

### 9.3 New surfaces the wave must additionally cover

Adaptation verdicts follow §5's vocabulary; ⚑ marks the ones needing a §11 ruling.

- **Watchdogs (FR-18)** ⚑ — world-scaled 50×26 chips in rows of 3 below the owner
  (OrgCanvas.tsx:202-231, :1323-1340), `font-size: 7px` two-line names (styles.css:2207), all
  state `title=`-only, tap-through to `WatchdogPanel`. At compact-map zoom a chip is ~20×10 CSS
  px. Proposed: HIDE from the compact map; REPLACE with a count-dot in the map caption + a
  watchdog list in the desk-sheet header (the panel itself becomes a full-bleed sheet like every
  overlay).
- **The ask system (asks.tsx, 613 lines)** — ask cards mount in the counter-scaled desk
  (desk.tsx:794-801, capped `max-height: 75%` styles.css:756) and in the user-inbox reading pane
  (App.tsx:1121-1133). D-123's sheet absorbs the desk mount at 1:1 — asks get *better* on
  compact. Two exceptions: the 18px-wide vertical credit-drag bar (styles.css:858, hover tip
  deliberately killed :868) falls under §6's drag-to-reallocate rule → REPLACE with the stepper
  at compact; `NulledAsk` renders uncapped in the inbox pane. The screen-space attention chrome
  (header bell App.tsx:492-507, eye pip cards.tsx:133) must survive the orgbar collapse (§11-E).
- **Edge-gated hire chips (F-03 sides + FR-25 top)** ⚑ — four `.hsof` sets now hover-gated AND
  cursor-nearest-edge-gated (`trackEdge` on every card pointermove, cards.tsx:726-735;
  `.sq.edge-t:hover > .hsof.side-t` styles.css:1179). **No touch equivalent exists** — a tap
  yields at most one move event, so the gate may never open. 22×22px targets held screen-constant
  by the new unclamped `--invzf` (OrgCanvas.tsx:1122). Confirms §5.1's REPLACE (full-screen hire
  form at compact) — but the form must now carry *placement*: below (child), side (ordering),
  above (FR-25 splice).
- **Drag-only granting** ⚑ — the org-mailbox tile is a world-scaled drop target
  (OrgCanvas.tsx:784-791) and `endNodeDrag` converts a card drop into an extern grant (:884-891);
  the UI names no tap alternative (mail.tsx:513). Audience granting by drag-to-eye predates the
  spec with the same property. Compact hides card drag entirely (§5.1) → a tap path must exist:
  proposed "+ holder" picker chip in the OrgInboxModal holders row (works for desktop too).
- **Retired stacks** — whole-stack drag (OrgCanvas.tsx:910-946) falls under the compact drag
  HIDE; `PilePicker` is the tap path and already planned as a bottom sheet. The stack margins
  (5/10/15px, hover-grown :985-987) stay desktop-mechanism; the count badge is the compact
  affordance (§5.1 unchanged).
- **Mail sparks** — decorative SVG circles rAF-animating over bezier paths (OrgCanvas.tsx:375-398,
  :1209-1216); each spark re-renders the canvas for ~420ms. ADAPT: cull with the §5.1 perf row
  (30Hz / culling), or HIDE at compact.
- **Doc chips / DocReader (FR-03)** — 21×21 world-scaled chips *outside* the card edge
  (styles.css:779, eating the 62px sibling gutter; titles `title=`-only docs.tsx:27); reader is a
  screen-space overlay (`min(760px, 92vw)`). ADAPT: chips fold into the sheet header's doc badges
  at compact (already exist, desk.tsx:508-513); reader becomes a full-bleed sheet; `.md table`
  needs an `overflow-x` wrapper.
- **Hierarchical tray (FR-16)** — bottom-left screen chrome, depth-indented (14px/level inside
  `max-width: 280px`), five `title=`-only row facts (OrgCanvas.tsx:1490-1515). It is §5.3's
  primary-navigation candidate — the promotion to a bottom-sheet roster stands, but the indent
  needs a cap (or path-abbreviation) and the title data must become row text.
- **Resume banner (D-122) + orgbar growth** ⚑ — the orgbar now carries 8-12 chips plus a
  three-item frozen-agents banner (App.tsx:419-467) that wraps to 3-4 rows on narrow viewports
  and pushes the canvas down with nothing re-measuring (§2-⑦). The §5.4 one-row collapse must
  absorb: banner (→ status chip; resume-all + auto-resume toggle into ⋯), bell (stays visible —
  it is the attention surface), working-count, connectivity.
- **Card badge growth (FR-23 `turnago`, unstick, HALTED, remote)** — `.sq-badges` gained a
  timestamp, a *clickable* unstick button (~40×14 world px, warning `title=`-only,
  cards.tsx:868-885) and remote/HALTED labels. The compact map LOD (§5.1) replaces the card face
  wholesale — its caption should carry a status dot + `turnago`; unstick moves to the sheet
  header (it already exists there, desk.tsx:477-486).
- **Desk-internal additions (FR-20 pinned message, nav chips, SlashHints, effort popover,
  notice/sealed lines, file cards, `convo.ts` windowing status rows)** — all inside `.desk-inner`,
  all inherit D-123's 1:1 sheet for free. One check: the pinned chip's manual `scrollTo`
  (desk.tsx:638-643) and `.jumpbottom` assume the desk's own scroller — they survive the sheet
  but must be re-verified after the portal (§2-⑧'s moved-hazard note).
- **Eye desk full-aspect expansion** — the focused eye card now expands to the viewport aspect
  (OrgCanvas.tsx:1228-1230) with `innerW = (eyeW-4)/(DESK_SCALE·dpi)` (cards.tsx:247): on a
  375px phone that is a ~2,800px-virtual panel — tabs at ~2px. Moot at compact (switchboard is
  HIDE per §5.1) but reinforces the min-side gate for medium.
- **Remote control (FR-01)** — gear-panel rows + a card badge whose semantics are `title=`-only.
  Experimental (per the 2026-08-06 ruling it stays so); no mobile adaptation beyond the badge
  data joining the sheet header. It is NOT a substitute for this wave — it hands one agent's
  session to claude.ai, it does not operate the org.
- **livebus (D-124)** — every polled surface refetches on any mutation + ws change. On cellular
  this raises background volume; the sheet/roster surfaces ride the same bus (no new work, noted
  for §5.1's perf row).

### 9.4 New prerequisites (append to §3)

11. **`CreditBar` pointercancel aborts** — DONE in this audit (see 9.1).
12. Portal list is now 8 modals + tray + HUD; `ComposeModal` must portal *with* its parent or
    flatten into it; the 10 nested `ConfirmModal` sites ride the desk/modal portals.
13. `trackEdge`/`edge-*` gating needs a `(hover: none)` bypass — on touch the hire affordance
    must not depend on cursor proximity (superseded at compact by the hire form; medium tier
    still needs it).
14. `.md table` horizontal-scroll wrapper (transcript + DocReader).
15. `Z_MIN` extracted from the two `0.24` literals; export `SX/SY/PAD` for the map-LOD work.

---

## 10. What did NOT change

No responsive CSS appeared (`@media` count: one, `prefers-reduced-motion`), no `dvh`/`svh`, no
safe-area, no `overscroll-behavior`, no text-size-adjust, no pinch/touch handling, no resize or
visualViewport listener, no portal beyond `DraftScopeModal`. The §2 hard-parts constraints (①
focus, ② nesting, ③ counter-scale, ④ chip exclusivity, ⑤ arbitration, ⑥ pinch, ⑦ resize, ⑧ perf
traps) all stand as written. The build order (§8) stands with §9.4 spliced into steps 1-2.

---

## 11. Decisions reopened by the drift — RULED 2026-08-14 (D-125)

All five were put to the user the day of the drift audit; the wave itself remains HELD
("spec-refresh only" was the chosen scope — even §8 steps 1-2 wait for the go-ahead).

- **A. 1600×900 deskHost — RULED: card, coarse-pointer required.** §4-B's predicate becomes
  `deskHost = 'sheet'` iff `min(vpW, vpH) < 780` **AND** (`(pointer: coarse)` OR width ≤ 640).
  Fine-pointer desktops keep the in-card desk at every window size; the 772-vs-780 cliff is gone.
- **B. Watchdogs at compact — RULED: hide from the map.** The compact map shows only a count-dot
  in the owner's caption; the watchdog list (name, state, detail links) lives in the desk sheet's
  header; `WatchdogPanel` becomes a full-bleed sheet like every overlay.
- **C. Hire form carries placement — APPROVED.** The compact full-screen hire form gains a
  placement selector (below / side-ordering / above-splice) so F-03 + FR-25 semantics survive
  touch.
- **D. Tap path for granting — NOT approved** (offered same day, not selected). Granting remains
  drag-only; note the consequence for the builder: with card drag hidden at compact (§5.1),
  extern/audience granting has **no compact path at all** until this is revisited. Do not invent
  one — surface the gap when the wave builds.
- **E. Compact orgbar consolidation — NOT approved as proposed** (offered same day, not
  selected). The §5.4 one-row collapse stands as originally written, but the specific
  banner→chip + bell-in-row absorption was not taken; re-ask when the layout tier builds.
