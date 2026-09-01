# Security Policy

## Supported versions

Track Tidy is a single-user desktop app with one actively maintained line -
only the latest release is supported. Please update to the newest version
(the app checks for updates on startup) before reporting an issue.

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for a security vulnerability.

Instead, use GitHub's private reporting: go to the
[Security tab](../../security/advisories/new) and open a new draft
security advisory. This reports it privately to the maintainer only, before
any public disclosure.

Please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce it
- The app version and OS you tested on

You should get an initial response within a few days. Once a fix is out,
the advisory can be published and credited if you'd like.

## Scope

Track Tidy is a free, open-source desktop app - not a service handling
sensitive data at scale. Realistic areas of interest include (but aren't
limited to):
- The auto-updater (download/checksum verification, installer execution)
- Local credential storage (SoundCloud tokens via the OS keyring)
- Handling of data from external sources (iTunes/SoundCloud/AcoustID
  responses, Discord webhook payloads)

Third-party dependencies (Pillow, mutagen, requests, etc.) should be
reported directly to their own maintainers unless the vulnerability is in
how Track Tidy specifically uses them.
