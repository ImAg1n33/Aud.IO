# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Aud.IO, please report it privately
rather than opening a public issue.

**Contact:** Open a private security advisory on GitHub, or email the maintainer.

**Response time:** We aim to acknowledge reports within 48 hours and provide a
fix timeline within 5 business days.

## Scope

Issues covered by this policy include but are not limited to:

- Unauthorized access to user data (profiles, listening history)
- API key or credential leakage
- Prompt injection that causes unintended tool execution
- Memory data exposure across sessions
- Dependency vulnerabilities with known exploits

## Best practices for contributors

- Never commit real API keys or credentials. Use `backend/.env` (gitignored).
- The pre-commit hook runs `scripts/security_scan.py` — do not skip it.
- Review diffs for accidental credential inclusion before pushing.
- If you suspect a key has been committed, follow the rotation guide in
  `docs/security-playbook.md`.

## Supported versions

Only the latest commit on `main` is currently supported with security updates.
