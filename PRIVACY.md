# Privacy

Track Tidy is a local desktop tool - it doesn't run its own server, doesn't
have accounts, and doesn't track you across sessions. This page describes
the only two ways it ever sends anything about you or your usage
anywhere: automatic usage pings, and the explicit "Report track" button.
Everything else (your music files, your tags, your scan history) stays on
your own machine.

## Automatic usage pings

Track Tidy posts a small notification to a Discord channel the developer
(Kevin) controls, through a small relay the developer runs on Cloudflare
Workers, in these cases:

| When | What's sent |
|---|---|
| First launch on a new Windows account, or an update to a new version | Your Windows username, your OS (e.g. "Windows 11"), app version |
| Uninstalling on Windows | Your Windows username, "Windows", the version being removed |
| After a scan finishes (including a cancelled one) | Windows username, new/removed/total file counts, no-cover-match count, how many tracks matched via each of iTunes/SoundCloud, how many needed the AcoustID audio-fingerprint fallback, app version - no track/file names |
| After an extraction finishes | Windows username, files-moved/folders-removed counts, app version |
| After a quality analysis finishes | Windows username, green/orange/red counts, app version |
| A cover source (iTunes/SoundCloud/AcoustID) gets rate-limited | Windows username, which source, app version |
| A scan comes back with an unusually high no-cover-match rate | Windows username, the affected files' names and current/detected tags |
| The app crashes (an unhandled error) | Windows username, your OS, app version, and a full error traceback (which may include file paths from your library if the crash happened while processing a specific file) |

This exists so the developer knows the app is actually being used and can
spot problems (a source going down, a matching failure pattern) without
needing you to file a bug report. It is not used for advertising, not sold,
and not shared with anyone beyond what's described here.

**Where it goes:** a single Discord channel, via Discord's own
infrastructure (Discord Inc., a US company) - the same as posting a message
in any Discord server. Track Tidy has no database of its own; the message
history in that channel *is* the record. Messages aren't deleted on any
schedule, so treat them as kept indefinitely unless removed by hand (see
"Deletion" below).

On the way, each ping goes through the developer's relay (`relay/worker.js`
in this repo, hosted on Cloudflare Workers - Cloudflare Inc., a US company).
Like any web request, Cloudflare sees your IP address. The relay checks the
ping looks like one of the app's own (see the table above), forwards it to
Discord, and keeps no log of it. To show a running "unique users" count on
the "new install" ping, it keeps one thing: a keyed hash (HMAC-SHA-256,
with a secret key that only exists on the relay) of each Windows username it
has seen - not the username itself, and not something that can be turned
back into it by guessing common names without that key - in Cloudflare's
key-value storage. To limit fake counts it also keeps, for one day only, how
many new users each IP address (also hashed with that key) added. The relay exists because the app used to post to Discord directly
with a link anyone could pull out of the installer, which was abused in
November 2026 to spam the channel. The uninstall ping is Windows-only - it's sent by the installer itself (see
`installer.iss`), not the app, since there's no equivalent hook on macOS
(dragging the app to Trash runs no code at all).

## "Report track" button

Pressing **Report track** on a specific row always sends that track's
filename, current/detected artist and title, and its cover art (existing
and/or suggested) to the same Discord channel, since pressing the button is
itself the explicit request to send that information. This is how
mismatched covers/tags get fixed.

## What's never sent

Your actual audio files, your full library contents or folder structure,
any tags/covers for tracks you haven't explicitly reported, and (outside
the no-cover-match batch report above) any filenames from a normal scan.

## Third-party services used to do the app's job

Looking up artist/title/cover art means Track Tidy talks to iTunes,
SoundCloud, and (as a last resort) AcoustID's audio-fingerprinting
service - each request includes only what's needed to search (a filename-
derived artist/title guess, or a short audio fingerprint for AcoustID), sent
directly from your machine to that service, not routed through the
developer. Each of these is a separate company with its own privacy
policy; Track Tidy doesn't control what they do with a search request on
their end. GitHub is contacted to check for app updates.

## Deletion / access requests

Since there's no account or database, there's nothing to look up by
"account" - but if you want the developer to remove past Discord messages
that included your Windows username (and its hash on the relay), open a
[GitHub Issue](https://github.com/k3rwan/track-tidy/issues) (or find another
contact method on the repo) naming the username, and they'll be located and
deleted by hand.

## Opting out

There's currently no in-app setting for this. Track Tidy is open source
(GPL-2.0-or-later - see [LICENSE](LICENSE)) though: building it from source
yourself, without the developer's shared credentials
(`default_credentials.json` - see the README's "Setup" section), never
talks to the developer's relay or Discord channel at all, since the relay
URL would simply be empty.

---

This page describes actual practice, not a legal certification of
compliance with any specific privacy law - if you have a formal
requirement, evaluate it against the description above, or reach out via
the Issues page with questions.
