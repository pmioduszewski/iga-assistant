---
name: dev-tools
description: Extract developer libraries, tools, and techniques from a dev-focused newsletter stream and score relevance against active software projects.

trigger:
  gmail_label: "Newsletter/Dev"

interest_profile: |
  Libraries, frameworks, tools, and techniques that could meaningfully improve
  software projects or engineering practice — especially anything related to
  performance, developer experience, observability, databases, AI/ML tooling,
  and open-source ecosystem trends. Blog posts and talks that introduce a
  concrete new approach or tool are also in scope. Marketing copy, job
  listings, and pure announcements without technical substance are out of scope.

scoring_context:
  - "projects/*"

fit_threshold: 2

output_wing: "vault/dev-tools"

cadence: on-demand

status: active
---

## Additional hook context

**Include:** GitHub repos, npm/PyPI/crates packages, CLI tools, hosted
services with a developer API, technical blog posts with a clear
take-away, conference talks linked to a recording or transcript.

**Exclude:** pure product-launch announcements without technical detail,
social-media threads with no linked artifact, newsletter meta-content
(e.g. "subscribe to our Slack").

**Scoring note:** a library that directly addresses a known pain point in
an active project scores 3; a broadly useful tool in the same technology
area scores 2; tangentially related tooling scores 1 and is dropped by the
default fit threshold.
