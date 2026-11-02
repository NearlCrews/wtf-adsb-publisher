# Security policy

## Report privately

On GitHub, use this repository's **Security → Report a vulnerability** entry for
a suspected vulnerability or a disclosure of private receiver data. This requires
the repository owner to enable
[private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository).

If that entry is unavailable, open an issue asking for a private reporting
channel without including vulnerability details, affected data, or credentials.
Wait for a private channel before sharing sensitive material. An ordinary public
bug report is appropriate for a sanitized functional defect.

Include the affected version, a minimal synthetic reproduction, expected and
observed behavior, the likely impact, and relevant sanitized logs. Never send
live credentials, databases, private hostnames, receiver coordinates, or
unredacted flight archives. Maintainers review reports as time permits; there is
no guaranteed response or remediation deadline.

## Supported versions

Security fixes target the active development branch and the latest released
version. Older releases have no promised backports. Until a release is
published, the development branch is the only supported line.

## Trust boundaries

Installed Python plugins are trusted code with the process's filesystem and
network permissions. Review a plugin before installing it, and run the publisher
as an unprivileged user with access limited to its feed, state, and providers.

Keep private state directories at mode 0700 and secret files, such as the Ghost
Admin API key and the OpenAI key, at mode 0600. Reach Ghost administration, SSH,
and service-to-service publishing over a private network. Ghost's stock front
end can put a private admin origin into every page through its Portal, search,
and comments scripts, so `deploy/ghost/ghost.production.example.json` disables
them. Keep them disabled on any public site.

A missing or invalid spending store must stop paid work. Restore its history
instead of starting a new store at zero. Public records omit receiver
coordinates and receiver-relative measurements, but aircraft identity and tracks
can still suggest the reception region. See [operations](docs/operations.md) for
recovery and privacy checks.
