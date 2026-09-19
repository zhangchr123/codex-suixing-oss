"""Private, per-user desktop configuration. No login credentials are needed here."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def default_state(platform=None, home=None):
    platform, home = platform or sys.platform, Path(home or Path.home())
    if platform == "darwin":
        return home / "Library/Application Support/CodexSuixing"
    if platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "CodexSuixing"
    return Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "codex-suixing"


def state_path(value=None):
    return Path(value or os.environ.get("CODEX_SUIXING_STATE_DIR") or default_state()).expanduser().resolve()


def private_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate(config):
    config = dict(config)
    host = config.get("sshHost", "")
    if not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.@:-]*", host):
        raise ValueError("SSH 主机请填写 ~/.ssh/config 中的别名，例如 my-relay")
    url = urlsplit(config.get("url", ""))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
        raise ValueError("网页地址必须是 HTTPS，例如 https://relay.example.com/")
    try:
        url.port
    except ValueError as error:
        raise ValueError("网页端口无效") from error
    context = config.get("contextThreadId", "")
    if context and not UUID.fullmatch(context):
        raise ValueError("上下文任务 ID 应为完整的小写 UUID")
    if not isinstance(config.get("control", False), bool):
        raise ValueError("control 必须是 true 或 false")
    if config.get("control") and not context:
        raise ValueError("启用发送前，请填写自己的 Codex 上下文任务 ID")
    for key in ("remotePython", "codexHome", "desktopEndpoint", "nodePath", "hostname"):
        value = config.get(key, "")
        if not isinstance(value, str) or any(c in value for c in "\n\r\x00"):
            raise ValueError(f"{key} 无效")
    config.setdefault("remotePython", "python3")
    config.setdefault("codexHome", os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config["url"] = config["url"].rstrip("/") + "/"
    return config


def read_config(state):
    return validate(json.loads((Path(state) / "connection.json").read_text(encoding="utf-8")))


def save_config(state, config):
    config = validate(config)
    private_write(Path(state) / "connection.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return config


def bridge_env(state, config):
    env = dict(os.environ)
    env["CODEX_SUIXING_STATE_DIR"] = str(state)
    env["CODEX_SUIXING_CONTEXT_THREAD_ID"] = config.get("contextThreadId", "")
    if config.get("desktopEndpoint"):
        env["CODEX_APP_TOOLS_PIPE_PATH"] = config["desktopEndpoint"]
    if config.get("nodePath"):
        env["CODEX_SUIXING_NODE"] = config["nodePath"]
    return env
