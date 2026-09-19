"""Check tracked and untracked non-ignored source before commit or packaging."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    actual = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip()
    if Path(actual).resolve() != ROOT:
        raise RuntimeError("Run only inside this project's own Git repository")
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    return sorted(set(name.decode("utf-8") for name in names.split(b"\0") if name))


def audit(names):
    problems = []
    patterns = [rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
                rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b", rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b",
                rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{35,}\b", rb"\bAKIA[A-Z0-9]{16}\b"]
    for name in names:
        path = ROOT / name
        if not path.is_file():
            continue
        fixture = name == "testdata/localhost-test-only.pem"
        if any(part in {".state", "downloads", "__pycache__"} for part in path.relative_to(ROOT).parts):
            problems.append(name + ": runtime/generated data")
        if path.suffix.lower() in {".pem", ".key", ".crt", ".p12", ".jks", ".apk", ".sqlite3", ".db", ".log"} and not fixture:
            problems.append(name + ": credential, state or binary file")
        if name.endswith("connection.json"):
            problems.append(name + ": private connection config")
        data = path.read_bytes()
        for pattern in patterns:
            if re.search(pattern, data) and not fixture:
                problems.append(name + ": possible secret (value omitted)")
                break
    return problems


if __name__ == "__main__":
    names = source_files()
    issues = audit(names)
    if issues:
        raise SystemExit("\n".join(issues))
    print(f"Source audit passed: {len(names)} files; public test-only TLS fixture exempted.")
