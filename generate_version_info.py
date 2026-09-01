"""Generates version_info.txt (a Windows VERSIONINFO resource) for
PyInstaller's --version-file, from src/track_tidy.py's own APP_VERSION -
keeps the built exe's file properties (publisher/product/description) in
sync with the app's version instead of shipping a blank/anonymous-looking
binary. Several heuristic/ML antivirus engines (e.g. DeepInstinct's plain
"MALICIOUS" verdict, no real signature) treat missing version metadata as
a mild suspicion signal - see the 0.28.2 VirusTotal investigation.

Run before pyinstaller (build_all.bat/build_exe.bat both do this
automatically); its output, version_info.txt, is gitignored like every
other build artifact.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import track_tidy

VERSION_INFO_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple!r},
    prodvers={version_tuple!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'KEVZ'),
         StringStruct(u'FileDescription', u'Track Tidy - DJ library auto-tagger'),
         StringStruct(u'FileVersion', u'{version}'),
         StringStruct(u'InternalName', u'Track-Tidy'),
         StringStruct(u'LegalCopyright', u'\\u00a9 KEVZ'),
         StringStruct(u'OriginalFilename', u'Track-Tidy.exe'),
         StringStruct(u'ProductName', u'Track Tidy'),
         StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def version_tuple(version_string):
    """PyInstaller's version resource needs a fixed 4-int tuple - our own
    APP_VERSION is usually 2 or 3 dotted segments (e.g. "0.28", "0.26.3"),
    so pad with zeros rather than requiring every version string to
    already have exactly four parts."""
    parts = [int(p) for p in version_string.split(".")]
    parts += [0] * (4 - len(parts))
    return tuple(parts[:4])


def main():
    version = track_tidy.APP_VERSION
    content = VERSION_INFO_TEMPLATE.format(version_tuple=version_tuple(version), version=version)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version_info.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Wrote {out_path} (version {version})")


if __name__ == "__main__":
    main()
