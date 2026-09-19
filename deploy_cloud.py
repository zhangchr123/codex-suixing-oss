"""Publish application files to a provisioned SSH host; runtime state is preserved."""
import argparse
import base64
import json
from pathlib import Path
import shlex
import subprocess
from settings import state_path, validate

ROOT = Path(__file__).resolve().parent
FILES = ["viewer.py", "control.py", "images.py", "android_auth.py", "chatgpt_mirror.py", "index.html", "app.js", "polling.js", "android.html", "android.js", "vendor/markdown-it.min.js", "vendor/markdown-it.LICENSE"]


def remote(profile, command, data=None):
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes"]
    if profile.get("hostname"):
        args += ["-o", "HostName=" + profile["hostname"]]
    result = subprocess.run(args + [profile["sshHost"], command], input=data, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace") or result.stdout.decode("utf-8", "replace"))
    return result.stdout


def upload(profile, files):
    code = """import base64,json,pathlib,sys,os
root=pathlib.Path.home()/'.local/share/codex-viewer'
for name,value in json.load(sys.stdin).items():
    path=root/name
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('Invalid path')
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.upload')
    temp.write_bytes(base64.b64decode(value))
    temp.chmod(0o600)
    os.replace(temp,path)
"""
    payload = json.dumps({n: base64.b64encode(v).decode() for n, v in files.items()}).encode()
    remote(profile, shlex.quote(profile.get("remotePython", "python3")) + " -c " + shlex.quote(code), payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, default=state_path() / "connection.json")
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    profile = validate(json.loads(args.connection.read_text(encoding="utf-8")))
    files = {name: (ROOT / name).read_bytes() for name in FILES}
    if args.apk:
        files["downloads/codex-suixing.apk"] = args.apk.read_bytes()
    upload(profile, files)
    if args.restart:
        remote(profile, "sudo -n /usr/bin/systemctl restart codex-viewer.service")
        print(remote(profile, "systemctl is-active codex-viewer.service").decode().strip())
    print("Application published to", profile["sshHost"])


if __name__ == "__main__":
    main()
