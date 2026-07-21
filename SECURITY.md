# Security Policy

## Reporting a vulnerability

Please do not open public issues for security vulnerabilities.

Report them privately via GitHub's ["Report a vulnerability"](../../security/advisories/new)
form on this repository. Include steps to reproduce and the potential
impact. You can expect an acknowledgement within a few days.

## Scope notes

- ReNest handles student PII (emails, display names, campus affiliation)
  and coordination messages between users. Reports touching data exposure
  across accounts or campuses are the highest priority.
- The AI concierge is sandboxed to read-only marketplace tools; prompt
  injection that causes writes, cross-user data access, or secret
  disclosure is in scope and taken seriously.
- Denial-of-service reports are in scope where a single request or small
  request volume causes disproportionate resource use (e.g., unbounded
  queries, missing rate limits).

## Supported versions

The `main` branch is the only supported version.
