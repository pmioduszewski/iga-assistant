# Daily Commands Pack

Defines daily workflow commands accessible via `/gaia <command>`.
Add this to `rules/commands.md` after installing, or use as-is.

## /gm

Good Morning — daily wake-up briefing.

1. `mempalace_status` — wake up
2. `mempalace_diary_read("gaia", last_n=3)` — load recent context
3. `mempalace_search` for user identity
4. Check calendar for today's events
5. Check tasks for today, highlight overdue
6. Search MemPalace for active project flags
7. Start the response with "📅 [Day], [Month] [Date] — Good Morning"

## /back

Welcome Back — mid-day re-entry briefing.

1. `mempalace_status` — wake up
2. `mempalace_diary_read("gaia", last_n=1)` — load most recent session
3. Check calendar for remaining events today
4. Check tasks for remaining work today
5. Start the response with "🔄 [Day], [Month] [Date] — Welcome Back"

## /eod

End of Day — session wrap-up and diary write.

1. Review this session for any facts not yet persisted to MemPalace — store them now
2. Update tasks — mark completed, review remaining
3. `mempalace_diary_write` — write session summary in AAAK format
4. Flag anything important for tomorrow's `/gaia gm`

## /focus

Focus on a project — load context from MemPalace and connected tools.
Usage: `/gaia focus <project-name>`

1. `mempalace_search` in `projects/<project-name>` for project context
2. Search MemPalace for recent decisions related to this project
3. Check connected project management tools for open issues/tasks
4. Read `rules/<project-name>.md` if it exists for project-specific preferences

## /plan

Plan — propose prioritized time blocks for the day.

1. Check calendar for today's fixed events (meetings, appointments)
2. Check tasks for today's priorities
3. Search MemPalace for user schedule preferences and habits
4. Propose time blocks around fixed events, prioritizing by urgency

## /brief

Brief — structured sync across all domains.

1. Check calendar — recent and upcoming events
2. Check tasks — status across all projects
3. Search MemPalace for active project flags and pending decisions
4. Present a structured sync report
5. After briefing: update MemPalace with any new context surfaced
