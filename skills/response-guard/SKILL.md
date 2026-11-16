---
name: response-guard
description: Keep everyday replies brief with a local word counter and one corrective continuation.
triggers:
  - kind: hook
    spec: UserPromptSubmit and Stop in Claude Code or Codex
status: shipped
---

# Response guard

Answer first, using the fewest words that fully address the request. Default:
150 words. Inline code and bare URLs count; markdown symbols (table pipes,
list dashes, rules), the URL part of a markdown link and fenced code blocks
do not. A fenced block is a copy-paste deliverable (prompt, command, code):
never shorten it to fit the limit. Explicit
requests for detail bypass the limit for that turn. Keep essential facts,
citations and required follow-up lines. Complete the work before summarizing it.

Offer one short, specific follow-up only when omitted detail would help the
current conversation. Avoid a habitual "Want more details?" closing.

`engine/guard.py` reinforces the rule at prompt submission and counts the final
assistant message at Stop. It requests at most one rewrite, using the existing
model. It never truncates text and makes no network or memory calls.

See [setup and limits](docs/setup.md). User preferences belong in
`SKILL.local.md`; the numeric cap is selected when installing the hooks.
