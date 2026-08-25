# Privacy

Track Tidy is a local desktop tool - it doesn't run its own server, doesn't
have accounts, and doesn't track you across sessions. This page describes
the only two ways it ever sends anything about you or your usage
anywhere: automatic usage pings, and the explicit "Report track" button.
Everything else (your music files, your tags, your scan history) stays on
your own machine.

## Automatic usage pings ("Send anonymous usage data")

Controlled by the **"Send anonymous usage data to the developer"** checkbox
in Settings - on by default, uncheck it at any time to stop all of this
immediately.

While it's checked, Track Tidy posts a small notification to a Discord
channel the developer (Kevin) controls, via a webhook, in these cases:

| When | What's sent |
|---|---|
| First launch on a new Windows account | Your Windows username, app version |
| After a scan finishes (including a cancelled one) | Windows username, new/removed/total file counts, no-cover-match count, app version - no track/file names |
| After an extraction finishes | Windows username, files-moved/folders-removed counts, app version |
| After a quality analysis finishes | Windows username, green/orange/red counts, app version |
| A cover source (iTunes/Spotify/SoundCloud/AcoustID) gets rate-limited | Windows username, which source, app version |
| A scan comes back with an unusually high no-cover-match rate | Windows username, the affected files' names and current/detected tags |

This exists so the developer knows the app is actually being used and can
spot problems (a source going down, a matching failure pattern) without
needing you to file a bug report. It is not used for advertising, not sold,
and not shared with anyone beyond what's described here.

**Where it goes:** a single Discord channel, via Discord's own
infrastructure (Discord Inc., a US company) - the same as posting a message
in any Discord server. Track Tidy has no database of its own; the message
history in that channel *is* the record. Messages aren't deleted on any
schedule, so treat them as kept indefinitely unless removed by hand (see
"Deletion" below). One of these fields (a running "unique users" count on
the "new install" ping) is computed by reading back that same channel's
past messages - see `count_unique_discord_users()` in `track_tidy.py`.

## "Report track" button

Pressing **Report track** on a specific row always sends that track's
filename, current/detected artist and title, and its cover art (existing
and/or suggested) to the same Discord channel - regardless of the checkbox
above, since pressing the button is itself the explicit request to send
that information. This is how mismatched covers/tags get fixed.

## What's never sent

Your actual audio files, your full library contents or folder structure,
any tags/covers for tracks you haven't explicitly reported, and (outside
the no-cover-match batch report above) any filenames from a normal scan.

## Third-party services used to do the app's job

Looking up artist/title/cover art means Track Tidy talks to iTunes,
Spotify, SoundCloud, and (as a last resort) AcoustID's audio-fingerprinting
service - each request includes only what's needed to search (a filename-
derived artist/title guess, or a short audio fingerprint for AcoustID), sent
directly from your machine to that service, not routed through the
developer. Each of these is a separate company with its own privacy
policy; Track Tidy doesn't control what they do with a search request on
their end. GitHub is contacted to check for app updates.

## Deletion / access requests

Since there's no account or database, there's nothing to look up by
"account" - but if you want the developer to remove past Discord messages
that included your Windows username, open a
[GitHub Issue](https://github.com/k3rwan/track-tidy/issues) (or find another
contact method on the repo) naming the username, and they'll be located and
deleted by hand.

## Opting out entirely

Besides the Settings checkbox, Track Tidy is open source
(GPL-2.0-or-later - see [LICENSE](LICENSE)): building it from source
yourself, without the developer's shared credentials
(`default_credentials.json` - see the README's "Setup" section), never
talks to the developer's Discord channel at all, since the webhook URL
would simply be empty.

---

This page describes actual practice, not a legal certification of
compliance with any specific privacy law - if you have a formal
requirement, evaluate it against the description above, or reach out via
the Issues page with questions.
