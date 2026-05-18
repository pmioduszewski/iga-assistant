import Foundation

// MARK: - Tooltip copy (plain English — the "non-technical spouse" bar)
//
// Every hover explanation in the dropdown is plain, jargon-free English a
// non-technical person understands in one sentence. No words like
// idempotency, ledger, dedup, circuit breaker, governor, tick, claim,
// dispatch, WORKER_REQUEST. Presentation only — surfaced via SwiftUI
// `.help(…)`; never affects engine, status, or contract logic.

enum HelpText {

    // Counts row
    static let queued =
        "Tasks Iga found to prep and lined up. They're waiting their turn "
        + "to run quietly in the background."
    static let running =
        "Background tasks Iga is working on right now."
    static let done =
        "Background tasks Iga has finished recently."

    // Status pill (was "Health")
    static let health =
        "Whether Iga is working normally. Green = just checked and all "
        + "good. Orange = the last check is a bit old, Iga may be busy or "
        + "between checks. Grey = Iga hasn't run yet. Red = something went "
        + "wrong reading Iga's notes."

    // Usage limits (was "Governor")
    static let governorSection =
        "Iga's usage limits. To avoid doing too much at once, Iga keeps "
        + "background work under a set cap. Nothing starts unless it fits "
        + "within the cap."
    static let breaker =
        "Tells you if Iga has room to do more right now. \"OK\" means "
        + "there's room. \"Paused\" means Iga has reached its limit for "
        + "now and will start again on its own once enough time passes — "
        + "there's nothing you need to do."
    static let invocations5h =
        "How much background work Iga has started in the last 5 hours "
        + "versus the most it's allowed. It fills up as Iga works and "
        + "empties as time passes."
    static let invocations24h =
        "How much background work Iga has started in the last day versus "
        + "the most it's allowed. It fills up as Iga works and empties as "
        + "time passes."
    static let estTokens5h =
        "Roughly how much thinking effort Iga has spent in the last 5 "
        + "hours versus the most it's allowed, so a busy stretch can't run "
        + "away."

    // Lined-up list (was "Queue")
    static let queueSection =
        "The tasks Iga has lined up to prep, waiting their turn."
    static let queueRow =
        "A task Iga plans to prep, with its name and which assistant it "
        + "will use."
    static let idempotencyKey =
        "A short name Iga gives this task so it only ever does it once and "
        + "never repeats the same prep."
    static let ledgerLine =
        "A running tally of Iga's tasks: how many are lined up, in "
        + "progress, and finished."

    // Last check (was "Last tick")
    static let tickSection =
        "What happened the last time Iga checked for things to prep."
    static let discovered =
        "How many of Iga's skills were checked for things to prep this "
        + "time round."
    static let fired =
        "Tasks Iga found that look worth prepping this time."
    static let condSkip =
        "Tasks Iga looked at but decided it's not the right moment for "
        + "yet. This is on purpose, not a problem."
    static let claimSkip =
        "Tasks Iga already handled recently, so it skipped them to avoid "
        + "repeating itself. This is good, not a problem."
    static let govDeny =
        "Tasks Iga held back for now to stay within its usage limits. "
        + "They'll get another chance once there's room."
    static let queueAlert =
        "More tasks than usual qualified this time round — just a "
        + "heads-up, not a problem."
    static let skillErrors =
        "One of Iga's skills had a setup mistake and was skipped. The "
        + "rest still ran fine — one bad skill never stops the others."

    // Skills section (#3)
    static let skillsSection =
        "The skills Iga checked this time round. Each one can line up "
        + "background tasks and/or show a widget below."
    static let skillRow =
        "A skill Iga checked, what it does in plain words, and whether it "
        + "found anything to do this time."

    // Widgets
    static let widgetsSection =
        "Little at-a-glance panels Iga's skills can show — like a habit "
        + "streak. Iga only displays them; each skill fills in its own."
    static let widgetWaiting =
        "This panel doesn't have any information to show yet. It'll fill "
        + "in once the skill has run once."
    static let widgetError =
        "This panel's information couldn't be read this time. It usually "
        + "fixes itself the next time the skill runs."
    static let widgetUnknownType =
        "This skill is showing a kind of panel this version of Iga "
        + "doesn't know how to draw yet. Updating the app will fix it."
    static let widgetGridLegend =
        "Each square is a day. Darker green means the habit was kept up "
        + "more strongly around that day; an empty square means it wasn't "
        + "done that day."
    static let widgetCoach =
        "A short, friendly note worked out from your own history — an "
        + "encouragement when you're on a roll, a gentle nudge when it's "
        + "been a while."

    // Habits widget (Wave B — multi-habit grid)
    static let habitsSection =
        "All your habits at a glance. Click any day's square to mark it "
        + "done or undo it — Iga keeps the counting; the app just shows it."
    static let habitsModeTabs =
        "Switch between a compact list (each habit's last week) and a full "
        + "grid view. Iga remembers which one you picked."
    static let habitsDenseScroll =
        "The grid stays the same height no matter how long a period you "
        + "pick — longer periods just scroll sideways."
    static let habitsWaiting =
        "No habits to show yet. Once you import from HabitKit (or log a "
        + "day), they'll appear here."
    static let habitsInverse =
        "This is a habit you're trying to AVOID. A filled-in square means "
        + "you stayed clean that day — that's the good outcome."
    static let habitsLegend =
        "Each square is a day. A filled square means you did it; darker "
        + "means a stronger run around that day. Empty means not done."
    static let habitsLegendInverse =
        "Each square is a day. A filled square means you stayed clean "
        + "(success). An empty one means a slip, or not logged."

    // The board column (Wave C v2 — the RIGHT half of the unified panel,
    // shown automatically beside the fundamentals on a single click; there
    // is no manual control for it anymore).
    static let researchSurfacing =
        "Things Iga dug up on its own that are worth a look — they'll also "
        + "show up in your next briefing."

    // Actions
    static let scanNow =
        "Check right now for things to prep, then refresh what's shown "
        + "here."
    static let openStateFile =
        "Open the file where Iga keeps its working notes, in Finder."
    static let scheduling =
        "When on, Iga checks for things to prep on its own on a timer. "
        + "The note shows when the next check is due."
    static let launchAtLogin =
        "When on, Iga starts by itself when you log in. \"needs "
        + "approval\" means macOS Settings has to allow it first."

    // Footer
    static let footerTimestamps =
        "When Iga last checked, and when this window last refreshed what "
        + "it shows. Hover to see the exact times."
}
