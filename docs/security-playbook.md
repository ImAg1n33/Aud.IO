# Security Playbook

This document is the operational security solution for Aud.IO before open-sourcing.

## 1. Immediate containment checklist

Run these steps if any key or cookie may have been exposed in chat, screenshots, or commits.

1. Rotate LLM API keys immediately in the provider console.
2. Invalidate NetEase login state and refresh NETEASE_COOKIE.
3. Replace local secrets in backend/.env with newly issued values.
4. Ensure backend/.env is not tracked by git.

## 2. Verify sensitive files are not tracked

Run in repository root:

```powershell
git ls-files backend/.env
```

Expected output: empty.

If the file appears, untrack it without deleting local content:

```powershell
git rm --cached backend/.env
```

Then commit the cleanup.

## 3. If secrets were ever committed in history

1. Rotate the leaked credentials first.
2. Rewrite history using git-filter-repo or BFG.
3. Force-push protected branches only after team alignment.

Minimal example with git-filter-repo:

```powershell
git filter-repo --path backend/.env --invert-paths
```

## 4. Standard local secret policy

1. Real secrets only in backend/.env.
2. Template values only in backend/.env.example.
3. Never paste full keys/cookies into issues, PR comments, or chat logs.
4. Prefer short-lived tokens where possible.

## 5. NetEase cookie policy

1. Keep NETEASE_COOKIE local-only.
2. If QR login is unstable under Docker network constraints, manual cookie import is acceptable.
3. Refresh cookie periodically and after suspicious activity.

## 6. Pre-push checks

Run:

```powershell
git status
git diff --staged
```

Then run local secret scanner task:

- VS Code Task: Scan Secrets (Tracked Files)

Only push when scanner reports no findings.

## 7. Pre-commit hook (automated guardrail)

Install once per clone:

```powershell
git config core.hooksPath .githooks
```

Hook behavior:

1. Runs scripts/security_scan.py before every commit.
2. Blocks commit when potential secrets are detected.

Verify installation:

```powershell
git config --get core.hooksPath
```

Expected output:

```text
.githooks
```
