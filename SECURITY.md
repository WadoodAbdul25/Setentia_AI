# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this
repository. Do not include credentials, private repository content, or an
exploitable proof of concept in a public issue.

Include the affected component, impact, reproduction conditions, and a minimal
redacted example. Maintainers will acknowledge the report and coordinate a fix
and disclosure timeline through the private report.

## Credential handling

Sentia stores provider credentials in VS Code SecretStorage and passes them only
to its authenticated loopback sidecar. API keys, session cookies, local auth
caches, `.env` files, and runtime databases must never be committed.
