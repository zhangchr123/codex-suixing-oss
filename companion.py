"""Codex Suixing desktop controller for macOS, Windows and Linux (Python 3.11+)."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
from settings import ROOT, bridge_env, private_write, read_config, save_config, state_path

LABEL = "org.codexsuixing.companion"


def is_running(state):
    """Probe the actual sync lock, never trust a stale or recycled PID."""
    path = Path(state) / "sync.lock"
    if not path.exists():
        return False
    with path.open("r+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            return True
    return False


def worker_args(state):
    executable = Path(sys.executable)
    if os.name == "nt" and executable.with_name("pythonw.exe").exists():
        executable = executable.with_name("pythonw.exe")
    return [str(executable), str(ROOT / "companion.py"), "run", "--state-dir", str(state)]


def start(state):
    read_config(state)
    if is_running(state):
        return "同步已经在运行"
    with (state / "launcher.log").open("ab") as log:
        child = subprocess.Popen(worker_args(state), cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == "nt" else 0)
    for _ in range(30):
        if is_running(state):
            return "同步已启动"
        if child.poll() is not None:
            raise RuntimeError("启动失败，请查看 launcher.log 和 sync.log")
        time.sleep(0.1)
    raise RuntimeError("启动尚未确认，请查看日志和状态")


def stop(state):
    if not is_running(state):
        return "同步未运行"
    private_write(state / "stop.request", "stop\n")
    return "已请求停止；正在进行的传输和发送结束后退出"


def launch_agent(state):
    return {"Label": LABEL, "ProgramArguments": worker_args(state), "RunAtLoad": True,
        "WorkingDirectory": str(ROOT), "StandardOutPath": str(state / "launcher.log"),
        "StandardErrorPath": str(state / "launcher.log"), "ProcessType": "Background",
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")}}


def autostart(state, enabled):
    if sys.platform == "darwin":
        target = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
        domain = f"gui/{os.getuid()}"
        if enabled:
            read_config(state)
            if target.exists():
                subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
            private_write(target, plistlib.dumps(launch_agent(state)).decode())
            subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True, capture_output=True)
        elif target.exists():
            subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
            target.unlink()
    elif os.name == "nt":
        target = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/CodexSuixing.vbs"
        if enabled:
            read_config(state)
            command = subprocess.list2cmdline(worker_args(state)).replace('"', '""')
            private_write(target, 'CreateObject("WScript.Shell").Run "' + command + '", 0, False\n')
        else:
            target.unlink(missing_ok=True)
    else:
        raise RuntimeError("Linux 请使用自己配置的用户服务；start/run 可正常使用")
    return "已启用登录自启" if enabled else "已取消登录自启"


def run(state):
    config = read_config(state)
    os.environ.update(bridge_env(state, config))
    args = [str(ROOT / "viewer.py"), "sync", "--data-dir", str(state), "--log-file", str(state / "sync.log"),
        "--codex-home", config["codexHome"], "--ssh-host", config["sshHost"], "--remote-python", config["remotePython"]]
    if config.get("hostname"):
        args += ["--hostname", config["hostname"]]
    if config.get("control"):
        args += ["--control"]
    import viewer
    sys.argv = args
    viewer.main()


def doctor(state, desktop=False):
    config = read_config(state)
    checks = {"python": sys.version.split()[0], "ssh": bool(shutil.which("ssh")),
        "node": bool(shutil.which(config.get("nodePath") or "node")),
        "codexLogs": (Path(config["codexHome"]).expanduser() / "sessions").is_dir(),
        "running": is_running(state), "controlEnabled": config.get("control", False)}
    if desktop:
        from control import DesktopBridge
        os.environ.update(bridge_env(state, config))
        client = DesktopBridge(ROOT, state)
        try:
            result = client.status([])
            checks["desktop"] = {"connected": result.get("connected", False), "error": result.get("error", "")}
        finally:
            client.close()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "run", "start", "stop", "status", "doctor", "autostart"])
    parser.add_argument("--state-dir")
    parser.add_argument("--ssh-host")
    parser.add_argument("--url")
    parser.add_argument("--context-thread")
    parser.add_argument("--endpoint")
    parser.add_argument("--enable-control", action="store_true")
    parser.add_argument("--desktop", action="store_true", help="Doctor: probe local desktop without sending messages")
    parser.add_argument("--disable", action="store_true", help="Disable login autostart")
    args = parser.parse_args()
    state = state_path(args.state_dir)
    try:
        if args.command == "init":
            if (state / "connection.json").exists():
                raise ValueError("配置已存在，请使用桌面界面编辑，或选择不同的 --state-dir")
            save_config(state, {"sshHost": args.ssh_host or "", "url": args.url or "", "control": args.enable_control,
                "contextThreadId": args.context_thread or "", "desktopEndpoint": args.endpoint or ""})
            print("配置已保存：", state / "connection.json")
        elif args.command == "run":
            run(state)
        elif args.command == "start":
            print(start(state))
        elif args.command == "stop":
            print(stop(state))
        elif args.command == "status":
            print(json.dumps({"running": is_running(state), "stateDir": str(state)}, ensure_ascii=False))
        elif args.command == "doctor":
            print(json.dumps(doctor(state, args.desktop), ensure_ascii=False, indent=2))
        else:
            print(autostart(state, not args.disable))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
