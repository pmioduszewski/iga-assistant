"""SYNTHETIC HabitKit-shaped fixtures the tests author themselves.

PRIVACY (binding): tests NEVER read the real export and NEVER touch the real
``~/Gaia/state``. Every name here is neutral ("Reading", "Water", ...). These
dicts mirror the HabitKit export *shape* (field names + types) exactly so the
importer/exporter/stats are exercised against a faithful structure without any
private data ever entering the repo.
"""

from __future__ import annotations


def habitkit_export() -> dict:
    """A multi-entity HabitKit-shaped export covering every field + edge:

    * habit with day goal (per-day target) — "Reading"
    * inverse habit — "NoSnack"
    * archived habit — "OldThing"
    * habit with a week goal (requiredNumberOfCompletions) — "Gym"
    * habit whose goal CHANGED mid-history (two intervals, one ended) —
      "Water"  (early per-day target 1, later per-day target 2)
    * a category + mappings
    * a reminder
    * multi-completion-day amounts, an explicit 0-amount day, a completion
      logged near local midnight in a +120min zone (tz edge), notes present
      and null.
    """
    return {
        "habits": [
            {
                "id": "h-reading",
                "name": "Reading",
                "description": "pages",
                "icon": "book",
                "color": "indigo",
                "emoji": None,
                "archived": False,
                "isInverse": False,
                "orderIndex": 0,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            },
            {
                "id": "h-nosnack",
                "name": "NoSnack",
                "description": None,
                "icon": "leaf",
                "color": "red",
                "emoji": None,
                "archived": False,
                "isInverse": True,
                "orderIndex": 1,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            },
            {
                "id": "h-old",
                "name": "OldThing",
                "description": None,
                "icon": "clock",
                "color": "amber",
                "emoji": None,
                "archived": True,
                "isInverse": False,
                "orderIndex": 2,
                "createdAt": "2025-01-01T08:00:00.000000Z",
            },
            {
                "id": "h-gym",
                "name": "Gym",
                "description": None,
                "icon": "dumbbell",
                "color": "emerald",
                "emoji": None,
                "archived": False,
                "isInverse": False,
                "orderIndex": 3,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            },
            {
                "id": "h-water",
                "name": "Water",
                "description": None,
                "icon": "drop",
                "color": "sky",
                "emoji": None,
                "archived": False,
                "isInverse": False,
                "orderIndex": 4,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            },
        ],
        "completions": [
            # Reading: 3 consecutive days, one with amount 2, one note set.
            {
                "id": "c-r1",
                "date": "2026-05-14T00:00:00.000Z",
                "habitId": "h-reading",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
            {
                "id": "c-r2",
                "date": "2026-05-15T00:00:00.000Z",
                "habitId": "h-reading",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 2,
                "note": "double session",
            },
            {
                "id": "c-r3",
                "date": "2026-05-16T00:00:00.000Z",
                "habitId": "h-reading",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
            # tz edge: UTC instant 2026-05-15T23:00Z with +120min offset =>
            # local 2026-05-16T01:00 => civil date 2026-05-16 (NOT 05-15).
            {
                "id": "c-r-tz",
                "date": "2026-05-15T23:00:00.000Z",
                "habitId": "h-reading",
                "timezoneOffsetInMinutes": 120,
                "amountOfCompletions": 1,
                "note": None,
            },
            # NoSnack (inverse): one slip on 2026-05-15 (amount 1 = ate).
            {
                "id": "c-ns1",
                "date": "2026-05-15T00:00:00.000Z",
                "habitId": "h-nosnack",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
            # Gym: week goal needs 3; only 2 logged this week.
            {
                "id": "c-g1",
                "date": "2026-05-12T00:00:00.000Z",
                "habitId": "h-gym",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
            {
                "id": "c-g2",
                "date": "2026-05-14T00:00:00.000Z",
                "habitId": "h-gym",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
            # Water: early period (per_day_target 1) days, then later
            # period needs 2/day. Day 05-10 has amount 1 (ok early), day
            # 05-16 has amount 1 (FAILS later target 2), day 05-15 amount 0
            # (explicit zero-amount day).
            {
                "id": "c-w1",
                "date": "2026-05-10T00:00:00.000Z",
                "habitId": "h-water",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
            {
                "id": "c-w0",
                "date": "2026-05-15T00:00:00.000Z",
                "habitId": "h-water",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 0,
                "note": None,
            },
            {
                "id": "c-w2",
                "date": "2026-05-16T00:00:00.000Z",
                "habitId": "h-water",
                "timezoneOffsetInMinutes": 60,
                "amountOfCompletions": 1,
                "note": None,
            },
        ],
        "intervals": [
            # Reading: active day goal, 1/day.
            {
                "id": "iv-reading",
                "habitId": "h-reading",
                "startDate": "2026-01-01T00:00:00.000Z",
                "endDate": None,
                "type": "day",
                "requiredNumberOfCompletions": None,
                "requiredNumberOfCompletionsPerDay": 1,
                "unitType": "incremental",
                "streakType": "day",
                "allowExceedingGoal": True,
            },
            # NoSnack: inverse, day, threshold 1 (one slip breaks it).
            {
                "id": "iv-nosnack",
                "habitId": "h-nosnack",
                "startDate": "2026-05-01T00:00:00.000Z",
                "endDate": None,
                "type": "day",
                "requiredNumberOfCompletions": None,
                "requiredNumberOfCompletionsPerDay": 1,
                "unitType": "manual",
                "streakType": "day",
                "allowExceedingGoal": False,
            },
            # Gym: week goal, 3/week, no exceeding.
            {
                "id": "iv-gym",
                "habitId": "h-gym",
                "startDate": "2026-01-01T00:00:00.000Z",
                "endDate": None,
                "type": "week",
                "requiredNumberOfCompletions": 3,
                "requiredNumberOfCompletionsPerDay": 1,
                "unitType": "incremental",
                "streakType": "day",
                "allowExceedingGoal": False,
            },
            # Water: goal CHANGED. Early interval ended 2026-05-15
            # (per_day 1), later interval starts 2026-05-15 (per_day 2).
            {
                "id": "iv-water-early",
                "habitId": "h-water",
                "startDate": "2026-01-01T00:00:00.000Z",
                "endDate": "2026-05-15T00:00:00.000Z",
                "type": "day",
                "requiredNumberOfCompletions": None,
                "requiredNumberOfCompletionsPerDay": 1,
                "unitType": "incremental",
                "streakType": "day",
                "allowExceedingGoal": True,
            },
            {
                "id": "iv-water-late",
                "habitId": "h-water",
                "startDate": "2026-05-15T00:00:00.000Z",
                "endDate": None,
                "type": "day",
                "requiredNumberOfCompletions": None,
                "requiredNumberOfCompletionsPerDay": 2,
                "unitType": "incremental",
                "streakType": "day",
                "allowExceedingGoal": True,
            },
        ],
        "categories": [
            {
                "id": "cat-health",
                "name": "Health",
                "icon": "heart",
                "orderIndex": 0,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            }
        ],
        "categoryMappings": [
            {
                "id": "cm-1",
                "habitId": "h-gym",
                "categoryId": "cat-health",
                "orderIndex": 0,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            },
            {
                "id": "cm-2",
                "habitId": "h-water",
                "categoryId": "cat-health",
                "orderIndex": 1,
                "createdAt": "2026-01-01T08:00:00.000000Z",
            },
        ],
        "reminders": [
            {
                "id": "rem-1",
                "habitId": "h-reading",
                "weekdayIndices": [1, 2, 3, 4, 5],
                "hour": 19,
                "minute": 30,
            }
        ],
    }
