"""Package only tracked, audited sources; also produce a macOS .app source launcher."""
from pathlib import Path
import hashlib
import plistlib
import subprocess
import zipfile
from audit_source import ROOT, audit

VERSION = "1.0.0"


def put(archive, name, data, executable=False):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def main():
    actual = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    if actual.resolve() != ROOT:
        raise SystemExit("Not this project's Git root")
    names = [name.decode() for name in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0") if name]
    if not names:
        raise SystemExit("No tracked sources")
    problems = audit(names)
    if problems:
        raise SystemExit("\n".join(problems))
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    source = output / f"codex-suixing-{VERSION}-source.zip"
    mac = output / f"codex-suixing-{VERSION}-macos.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for name in names:
            put(archive, f"codex-suixing-{VERSION}/{name}", (ROOT / name).read_bytes(), name.endswith(".command"))
    base = "Codex Suixing.app/Contents/"
    launcher = '''#!/bin/zsh
set -eu
cd -- "${0:A:h}/../Resources/source"
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! python3 -c 'import sys, tkinter; assert sys.version_info >= (3, 11)' 2>/dev/null; then
  osascript -e 'display alert "Python 3.11+ with Tk is required" message "Install Python from python.org, then open Codex Suixing again."'
  exit 1
fi
exec python3 desktop.py
'''
    with zipfile.ZipFile(mac, "w") as archive:
        info = {"CFBundleName": "Codex Suixing", "CFBundleDisplayName": "Codex 随行", "CFBundleIdentifier": "org.codexsuixing.desktop",
                "CFBundleExecutable": "CodexSuixing", "CFBundlePackageType": "APPL", "CFBundleShortVersionString": VERSION,
                "CFBundleVersion": "1", "NSHighResolutionCapable": True}
        put(archive, base + "Info.plist", plistlib.dumps(info))
        put(archive, base + "MacOS/CodexSuixing", launcher.encode(), True)
        for name in names:
            put(archive, base + "Resources/source/" + name, (ROOT / name).read_bytes(), name.endswith(".command"))
        put(archive, "READ-ME.md", (ROOT / "docs/macos.md").read_bytes())
    (output / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n" for p in [source, mac]), encoding="ascii")
    print(source.name, mac.name)


if __name__ == "__main__":
    main()
