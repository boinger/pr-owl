# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities through [GitHub's private vulnerability reporting](https://github.com/boinger/pr-owl/security/advisories/new).

Do not open a public issue for security vulnerabilities.

You should receive a response within 48 hours. If the vulnerability is confirmed, a fix will be released as soon as possible.

## Scope

pr-owl executes `gh` CLI commands via subprocess. Security concerns include:

- Command injection via crafted PR titles or repo names
- Credential exposure through logging or error messages
- Subprocess calls with `shell=True` (this is explicitly prohibited in the codebase)

All subprocess interaction is isolated in `src/pr_owl/gh.py` with `shell=False` enforced.
