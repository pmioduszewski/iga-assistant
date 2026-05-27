# Iga — macOS menu-bar companion

A SwiftUI `MenuBarExtra` app (macOS 13+) that fronts the **frozen**
`iga-proactive` Python engine. It is the **scheduler-host + state viewer +
notifier** — the planned launchd thin-daemon, replaced by a menu-bar app.

- Product: `IgaMenuBar` · Bundle id: `com.iga.menubar` · App: `Iga.app`
- Menu-bar-only (`LSUIElement=true`): no Dock icon, no window.

## Hard contract (frozen — MemPalace `iga/decisions/3542bae6`)

**The engine decides. The app only renders state, relays OS events, and
triggers the engine. ZERO job/admission/idempotency/budget logic in Swift.**

The app's *only* permitted engine side effect is exec'ing the documented
scan command. It never writes the state JSON file and never writes the
sqlite ledger (opened strictly read-only).

**Deletion invariant:** deleting `Iga.app` removes only the scheduler host +
viewer + notifier. `/gm` calling the engine in-session continues to work with
**zero external infrastructure**. The app is a convenience front-end, never a
dependency. This is enforced by `ContractGuard.swift` + the
`ContractLitmusTests` suite (source-grep + runtime assertions).

## What it shows

A single 380pt dropdown, top to bottom:

- **Header:** "Iga · Proactive Engine" + a colored **health pill**
  (Healthy / Stale · Nm / Not run yet / Error).
- **Counts row:** big rounded Queued / Running / Done figures (Queued blue
  when >0, Running purple when >0, Done green).
- **Governor:** an `OK` / `TRIPPED` breaker capsule plus three meters
  (Invocations 5h, Invocations 24h, Est-tokens 5h) as `used / max` with a
  progress bar.
- **Queue:** count badge + up to 8 rows (job_id, short idempotency key,
  model) with a `+N more` overflow, then a one-line ledger tally
  (`N claimed · N running · N done`, or an `unavailable` note).
- **Last tick:** a two-column mini-grid (Discovered, Fired, Cond-skip,
  Claim-skip, Gov-deny) + an optional queue-alert label + a contained,
  count-summarized **skill-errors disclosure** (collapsed; never raw red
  text bleeding across the panel; zero errors renders nothing).
- **Surface:** the `/gm`/`/back` 📑 lines + overflow, shown only when
  `surfacer.refresh_state` populated them.
- **Actions:** Scan now · Open state file · Scheduling toggle (with a
  next-run hint) · Launch at login toggle (with status) · Quit Iga.
- **Footer:** relative "engine ran Xm ago · polled Ym ago" (absolute
  timestamps on hover) + a `scan ok` / `scan exit N` indicator.

Presentation is glanceable, not a dashboard: the health pill and governor
meters are colored green/amber/red by actual headroom (amber ≥70% of a
ceiling, red ≥90% or breaker tripped) — pure display mapping of values the
engine already computed, no admission logic in the UI. Works in light and
dark appearance.

## What it reads / runs

- State JSON (read-only): `$IGA_PROACTIVE_STATE` or
  `~/Gaia/scratch/proactive-state.json`
- Ledger (read-only, `?mode=ro` + `SQLITE_OPEN_READONLY`):
  `$IGA_PROACTIVE_DB` or `~/Gaia/state/proactive.db`
- The ONLY command it execs:
  `cd ~/Gaia/skills/iga-proactive && PYTHONPATH=engine uv run python -m engine scan --json`

Poll cadence: 15s default, override with `IGA_POLL_SECONDS`.

## Scheduler host (launchd replacement)

- `NSBackgroundActivityScheduler` — OS-coalesced periodic trigger
  (~6h interval, 2h tolerance ≈ a morning/evening cadence).
- `NSWorkspace.didWakeNotification` — fires ~30s after the Mac wakes, so an
  overnight-off machine still gets its morning scan when powered on.
- Menu toggle enables/disables; choice persists in `UserDefaults`.

## Build

Requires the Swift toolchain (Xcode / Swift 5.9+). From this directory:

```
./build.sh            # release build + assemble Iga.app
./build.sh debug      # debug build
```

This is the exact, reproducible build command. It does **no code signing and
no notarization** — intentional, per the frozen decision. Do not add them.

Run tests — **always pass `--enable-xctest`**:

```
swift test --enable-xctest
```

> Footgun: this is an **XCTest** package (no Swift Testing tests). On the
> toolchains verified here (Swift 6.2.x) plain `swift test` does run the 14
> XCTest cases — but on toolchains/configurations where Swift Testing is the
> default discovery path, plain `swift test` can **silently execute 0 XCTest
> cases and still exit 0** ("Test run with 0 tests … passed"), i.e. a false
> green. `--enable-xctest` makes XCTest discovery explicit and is the only
> form you should rely on in CI or before publishing. The suite includes
> `ContractLitmusTests` (source-grep + runtime assertions) which fails the
> build if any write/subprocess primitive escapes the single sanctioned
> `ContractGuard` entry point.

## Where it installs (discoverable as “Iga”)

Every `./build.sh` run **copies** the fresh bundle to:

```
~/Applications/Iga.app
```

`~/Applications` is the standard per-user apps location — **no sudo**. The
build re-runs `mdimport` on it so it is **Spotlight-findable immediately**:
press ⌘-Space, type **Iga**, Enter. It also appears in **Launchpad** (under
the "Other" group / search). The repo-local `./Iga.app` is kept too, for
development.

The install step is idempotent: each build does a `--delete` sync so the
installed copy is always byte-identical to the latest build (no stale files).

Run the app:

```
open ~/Applications/Iga.app     # installed copy (recommended)
open ./Iga.app                  # repo build (dev)
```

> Launchpad note: an unsigned app may take a moment to appear in Launchpad,
> or need a Dock restart — run `killall Dock`. If Launchpad still won't show
> it (a known macOS limitation for unsigned apps), it is always reachable via
> Spotlight ("Iga") or `open ~/Applications/Iga.app`. Spotlight indexing is
> reliable here; Launchpad is the only surface that can lag for unsigned apps.

## Uninstall (complete — do these in order)

Deleting the bundle alone leaves two OS registrations behind (a login item
and the Mac's record of the background scheduler). Full removal:

1. **Quit the running app.** Menu → **Quit Iga** (or
   `osascript -e 'quit app "Iga"'`, or `pkill -x IgaMenuBar`). The
   `NSBackgroundActivityScheduler` is not a launchd job — it lives only inside
   the running process, so quitting stops all scheduled scans immediately.
   There is nothing to `launchctl unload`.
2. **Unregister the login item *before* deleting the bundle.** Open the menu
   and toggle **Launch at login → off** (this calls `SMAppService.unregister()`
   on `SMAppService.mainApp`). If you delete the `.app` first, also clear the
   stale entry in **System Settings → General → Login Items** (remove "Iga").
3. **Delete both bundles:**
   ```
   rm -rf ~/Applications/Iga.app                  # installed copy
   rm -rf ~/Gaia/skills/iga-proactive/app/Iga.app # repo build
   ```
4. **(Optional) Clear persisted preferences.** The scheduling-enabled flag
   persists in the app's `UserDefaults` domain. To wipe it so a future
   reinstall starts fresh:
   ```
   defaults delete com.iga.menubar 2>/dev/null || true
   ```
   (Harmless to skip — a deleted app's defaults are inert.)

This removes only the scheduler host + viewer + notifier. Per the deletion
invariant above, `/gm` calling the engine in-session still works with zero
external infrastructure — the engine, ledger (`~/Gaia/state/proactive.db`),
and state file are untouched by an app uninstall.

## One-time human-only setup (unavoidable OS permission clicks)

These are macOS security gates — they cannot be scripted:

1. **Gatekeeper (first launch):** the app is unsigned & un-notarized by
   design. Double-clicking shows "cannot be opened". Fix: **right-click
   `~/Applications/Iga.app` → Open → Open** (once). This applies to the FIRST
   launch of the installed copy (and again if you ever launch the repo build
   separately — Gatekeeper tracks each bundle path). Thereafter it launches
   normally, including from Spotlight/Launchpad.
2. **Notifications:** on first launch macOS asks to allow notifications —
   click **Allow** (needed for new-job / breaker / done alerts).
3. **Launch at login:** open the menu → toggle **Launch at login**. If macOS
   says "requires approval", enable Iga under
   *System Settings → General → Login Items*.

Checklist: ☐ right-click→Open ☐ Allow notifications ☐ enable Login Item.

## Architecture (render / relay / trigger only)

| File | Role |
|---|---|
| `EngineState.swift` | Pure decoder for the v1 state contract |
| `LedgerReader.swift` | Read-only sqlite reader (driver-level RO guard) |
| `ContractGuard.swift` | The single sanctioned engine-exec entry point |
| `EngineRunner.swift` | Runs the one scan command |
| `StateStore.swift` | Poller + notification diffing (de-duped by idem key) |
| `Notifier.swift` | `UNUserNotificationCenter` wrapper |
| `Scheduler.swift` | Background activity + wake trigger (launchd replacement) |
| `LoginItem.swift` | `SMAppService` login-item management |
| `MenuContent.swift` / `IgaApp.swift` | SwiftUI MenuBarExtra |

No file outside `ContractGuard.swift` constructs a `Process`; no file writes
the state file or ledger. The test suite fails the build if that changes.
