# Third-party notices

Track Tidy bundles the following third-party software. Full license texts
for the copyleft entries are included alongside this file
(`LICENSE-GPLv2-mutagen.txt`, `LICENSE-GPLv3-FFmpeg.txt`,
`LICENSE-Chromaprint.txt`); permissive entries are summarized below with a
pointer to the license name.

## Copyleft (GPL/LGPL)

- **mutagen** 1.48.1 - GNU General Public License v2 or later
  (GPL-2.0-or-later). Copyright (C) 2005 Michael Urman and contributors.
  Full text: `LICENSE-GPLv2-mutagen.txt`.
- **FFmpeg** (bundled as `ffmpeg.exe`, gyan.dev "full" build) - built with
  `--enable-gpl --enable-version3`, therefore licensed under the GNU
  General Public License v3 (GPLv3). Full text:
  `LICENSE-GPLv3-FFmpeg.txt`. Track Tidy invokes it as a separate process
  (not linked into the app), but it is distributed alongside the app in
  the installer.
- **Chromaprint** (bundled as `fpcalc.exe`, official acoustid/chromaprint
  release build) - MIT-licensed on its own, but the distributed binary
  includes FFmpeg (LGPL 2.1) components, so the binary as a whole is
  licensed under LGPL 2.1. Full text: `LICENSE-Chromaprint.txt`. Invoked
  as a separate process (not linked into the app), used for the optional
  AcoustID audio-identification fallback.

Because mutagen is imported directly into the packaged application, this
whole project is licensed under the GNU General Public License v2 or
later - see the root [LICENSE](LICENSE) file. A matching source archive
is attached to every GitHub release alongside the installer.

## Permissive

- **Pillow** 12.3.0 - MIT-CMU License
- **requests** 2.34.2 - Apache License 2.0
- **tkinterdnd2** 0.6.2 - MIT License
- **urllib3** (requests dependency) - MIT License
- **certifi** (requests dependency) - Mozilla Public License 2.0
- **charset-normalizer** (requests dependency) - MIT License
- **idna** (requests dependency) - BSD 3-Clause License
- **keyring** 25.7.0 - MIT License (credential storage via the OS's
  native credential store - Windows Credential Manager, macOS Keychain)
- **platformdirs** 4.11.2 - MIT License (per-OS config directory)
- **jaraco.classes / jaraco.functools / jaraco.context** (keyring
  dependencies) - MIT License
- **more-itertools** (keyring dependency) - MIT License
- **pywin32-ctypes** (keyring dependency, Windows only) - BSD 3-Clause
  License
- **pyacoustid** 1.3.1 - MIT License (AcoustID web service client, used
  by the optional audio-identification fallback)
