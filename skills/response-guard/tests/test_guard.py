from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ENGINE = Path(__file__).parents[1] / "engine" / "guard.py"
SPEC = importlib.util.spec_from_file_location("response_guard", ENGINE)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def run_hook(
    tmp_path: Path,
    payload: object,
    *,
    host: str = "codex",
    raw: bool = False,
    max_words: int = 120,
):
    stdin = payload if raw else json.dumps(payload)
    proc = subprocess.run(
        [
            sys.executable,
            str(ENGINE),
            "--host",
            host,
            "--state-dir",
            str(tmp_path),
            "--max-words",
            str(max_words),
        ],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    return json.loads(proc.stdout) if proc.stdout else None


def prompt(session: str, text: str) -> dict[str, object]:
    return {"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": text}


def stop(session: str, text: str, active: bool = False) -> dict[str, object]:
    return {
        "hook_event_name": "Stop",
        "session_id": session,
        "stop_hook_active": active,
        "last_assistant_message": text,
    }


def words(count: int) -> str:
    return " ".join(f"w{i}" for i in range(count))


def test_threshold_is_whitespace_words_and_blocks_only_over_120(tmp_path: Path):
    run_hook(tmp_path, prompt("s120", "Summarize it"))
    assert run_hook(tmp_path, stop("s120", words(120))) is None

    run_hook(tmp_path, prompt("s121", "Summarize it"))
    blocked = run_hook(tmp_path, stop("s121", words(121)))
    assert blocked["decision"] == "block"
    assert blocked["reason"].startswith(guard.REWRITE_MARKER)


@pytest.mark.parametrize(
    "user_text",
    [
        "Explain this in detail",
        "Give me a thorough analysis",
        "I want an in-depth review",
        "Do a deep dive",
        "Show it step-by-step",
        "Elaborate",
        "Tell me more",
        "More details",
        "More details, please",
        "A deep dive, please",
        "Step by step, please",
        "Write 121 words",
        "Use 1,000 words",
    ],
)
def test_explicit_detail_requests_are_exempt(tmp_path: Path, user_text: str):
    context = run_hook(tmp_path, prompt(user_text, user_text))
    assert "word limit is exempt" in context["hookSpecificOutput"]["additionalContext"]
    assert run_hook(tmp_path, stop(user_text, words(250))) is None


@pytest.mark.parametrize(
    "user_text",
    [
        "Explain this",
        "Do not elaborate; explain it",
        "Don’t elaborate; explain it",
        "No detailed explanation, please",
        "Do not give me a detailed explanation",
        "No need for a detailed explanation",
        "Don't explain this in detail",
        "The detailed report is wrong",
        'What does "explain in detail" mean?',
        "The code says `deep dive`; summarize it",
        "Quote: 'step-by-step', then be concise",
        "What does “deep dive” mean?",
        "Write 120 words",
    ],
)
def test_non_explicit_negated_or_quoted_detail_is_not_exempt(tmp_path: Path, user_text: str):
    context = run_hook(tmp_path, prompt(user_text, user_text))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]
    assert run_hook(tmp_path, stop(user_text, words(121)))["decision"] == "block"


def test_one_retry_survives_codex_synthetic_prompt(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    first = run_hook(tmp_path, stop("session", words(121)))
    synthetic = run_hook(tmp_path, prompt("session", first["reason"]))
    context = synthetic["hookSpecificOutput"]["additionalContext"]
    assert "Rewrite the previous answer once" in context
    assert run_hook(tmp_path, stop("session", words(121))) is None


def test_duplicate_concurrent_stops_create_only_one_retry(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: run_hook(tmp_path, stop("session", words(121))), range(2))
        )
    assert sum(result is not None for result in results) == 1


def test_real_prompt_resets_retry(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "First request"))
    assert run_hook(tmp_path, stop("session", words(121)))["decision"] == "block"
    run_hook(tmp_path, prompt("session", "A genuinely new request"))
    assert run_hook(tmp_path, stop("session", words(121)))["decision"] == "block"


def test_stop_hook_active_never_blocks(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"), host="claude")
    assert run_hook(tmp_path, stop("session", words(121), active=True), host="claude") is None


def test_successful_active_rewrite_clears_offer_from_rejected_answer(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"), host="claude")
    rejected = words(121) + " Want the reasoning?"
    assert run_hook(tmp_path, stop("session", rejected), host="claude")["decision"] == "block"
    assert run_hook(
        tmp_path, stop("session", "Concise rewrite.", active=True), host="claude"
    ) is None
    context = run_hook(tmp_path, prompt("session", "yes"), host="claude")
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_short_approval_only_exempt_after_relevant_offer(tmp_path: Path):
    run_hook(tmp_path, prompt("offered", "Summarize"))
    answer = "Done. Want the reasoning?"
    assert run_hook(tmp_path, stop("offered", answer)) is None
    context = run_hook(tmp_path, prompt("offered", "yes please"))
    assert "word limit is exempt" in context["hookSpecificOutput"]["additionalContext"]
    assert run_hook(tmp_path, stop("offered", words(121))) is None

    run_hook(tmp_path, prompt("unrelated", "Summarize"))
    run_hook(tmp_path, stop("unrelated", "Done. Want another task?"))
    context = run_hook(tmp_path, prompt("unrelated", "go ahead"))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_yes_accepts_detail_offered_in_required_next_line(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    run_hook(tmp_path, stop("session", "Done.\n\nNext: Want the reasoning?"))
    context = run_hook(tmp_path, prompt("session", "yes"))
    assert "word limit is exempt" in context["hookSpecificOutput"]["additionalContext"]


def test_response_annotation_accepts_only_selected_detail_offer(tmp_path: Path):
    run_hook(tmp_path, prompt("detail", "Summarize"))
    run_hook(tmp_path, stop("detail", "Next: Want the reasoning?"))
    detail_annotation = (
        '<response-annotations>{"annotation":"Yes","selectedText":'
        '"Next: Want the reasoning?"}</response-annotations>'
    )
    context = run_hook(tmp_path, prompt("detail", detail_annotation))
    assert "word limit is exempt" in context["hookSpecificOutput"]["additionalContext"]

    run_hook(tmp_path, prompt("generic", "Implement it?"))
    run_hook(tmp_path, stop("generic", "Next: Should I implement it?"))
    generic_annotation = (
        '<response-annotations>{"annotation":"Yes","selectedText":'
        '"Next: Should I implement it?"}</response-annotations>'
    )
    context = run_hook(tmp_path, prompt("generic", generic_annotation))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_codex_annotation_array_with_header_accepts_detail(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    run_hook(tmp_path, stop("session", "Next: Want the reasoning?"))
    annotated = '# Response annotations:\n<response-annotations>\n' + json.dumps([
        {"text": "Next: Want the reasoning?", "annotation": "Yes", "source": {"messageId": "synthetic"}}
    ]) + '\n</response-annotations>\n\n## My request:\n'
    context = run_hook(tmp_path, prompt("session", annotated))
    assert "word limit is exempt" in context["hookSpecificOutput"]["additionalContext"]
    run_hook(tmp_path, stop("session", "Next: Want the reasoning?"))
    context = run_hook(tmp_path, prompt("session", annotated + "Just a brief answer."))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_negated_answer_is_not_recorded_as_detail_offer(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    run_hook(tmp_path, stop("session", "I don't want more details."))
    context = run_hook(tmp_path, prompt("session", "yes"))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_plain_answer_reference_is_not_mistaken_for_offer(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    run_hook(tmp_path, stop("session", "The reviewers want the reasoning documented."))
    context = run_hook(tmp_path, prompt("session", "yes"))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_detail_offer_expires(tmp_path: Path):
    run_hook(tmp_path, prompt("session", "Summarize"))
    run_hook(tmp_path, stop("session", "Done. Want the reasoning?"))
    with sqlite3.connect(tmp_path / "guard.sqlite3") as connection:
        connection.execute(
            "UPDATE session_state SET detail_offer_at = 1 WHERE session_id = 'session'"
        )
    context = run_hook(tmp_path, prompt("session", "please"))
    assert "no more than 120" in context["hookSpecificOutput"]["additionalContext"]


def test_numeric_detail_request_uses_configured_cap(tmp_path: Path):
    context = run_hook(
        tmp_path, prompt("session", "Write 150 words"), max_words=200
    )
    assert "no more than 200" in context["hookSpecificOutput"]["additionalContext"]
    assert run_hook(
        tmp_path, stop("session", words(201)), max_words=200
    )["decision"] == "block"


def test_sessions_and_hosts_are_isolated(tmp_path: Path):
    run_hook(tmp_path, prompt("same", "Detailed analysis"), host="codex")
    run_hook(tmp_path, prompt("other", "Summarize"), host="codex")
    run_hook(tmp_path, prompt("same", "Summarize"), host="claude")
    assert run_hook(tmp_path, stop("same", words(121)), host="codex") is None
    assert run_hook(tmp_path, stop("other", words(121)), host="codex")["decision"] == "block"
    assert run_hook(tmp_path, stop("same", words(121)), host="claude")["decision"] == "block"


def test_unknown_or_malformed_input_fails_open(tmp_path: Path):
    assert run_hook(tmp_path, stop("missing", words(121))) is None
    assert run_hook(tmp_path, {"hook_event_name": "Stop", "session_id": "x"}) is None
    assert run_hook(tmp_path, "{bad json", raw=True) is None


def test_database_never_stores_prompt_or_answer_content(tmp_path: Path):
    secret_prompt = "SECRET-PROMPT explain this"
    secret_answer = "SECRET-ANSWER " + words(121)
    run_hook(tmp_path, prompt("session", secret_prompt))
    run_hook(tmp_path, stop("session", secret_answer))

    database = tmp_path / "guard.sqlite3"
    raw = database.read_bytes()
    assert b"SECRET-PROMPT" not in raw
    assert b"SECRET-ANSWER" not in raw
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(session_state)")]
    assert columns == [
        "host",
        "session_id",
        "detail_allowed",
        "detail_offer",
        "detail_offer_at",
        "retry_used",
    ]


def test_disable_environment_makes_hook_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IGA_RESPONSE_GUARD_OFF", "1")
    proc = subprocess.run(
        [sys.executable, str(ENGINE), "--host", "codex", "--state-dir", str(tmp_path)],
        input=json.dumps(prompt("s", "hello")),
        text=True,
        capture_output=True,
        env=dict(__import__("os").environ),
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not (tmp_path / "guard.sqlite3").exists()
