"""Small native desktop controller; Python.org distributions include Tk on macOS."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk
import companion
from settings import read_config, save_config, state_path


def main():
    state = state_path()
    window = tk.Tk()
    window.title("Codex 随行 · 电脑端")
    window.geometry("800x660")
    window.minsize(660, 590)
    panel = ttk.Frame(window, padding=22)
    panel.pack(fill="both", expand=True)
    panel.columnconfigure(1, weight=1)
    ttk.Label(panel, text="Codex 随行", font=("", 23, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(panel, text="把这台电脑的对话同步到自己的服务器", padding=(0, 8, 0, 16)).grid(row=1, column=0, columnspan=2, sticky="w")
    fields = {}
    definitions = [("sshHost", "SSH 主机别名"), ("url", "HTTPS 网页地址"), ("codexHome", "Codex 数据目录"),
        ("contextThreadId", "上下文任务 ID"), ("desktopEndpoint", "桌面连接路径（Mac 必填）"),
        ("nodePath", "Node 路径（留空自动查找）"), ("remotePython", "服务器 Python")]
    config = {}
    if (state / "connection.json").exists():
        try:
            config = read_config(state)
        except (OSError, ValueError) as error:
            messagebox.showerror("配置错误", str(error))
    defaults = {"codexHome": os.environ.get("CODEX_HOME", str(Path.home() / ".codex")), "remotePython": "python3"}
    for row, (key, label) in enumerate(definitions, 2):
        ttk.Label(panel, text=label, padding=(0, 8, 12, 8)).grid(row=row, column=0, sticky="w")
        variable = tk.StringVar(value=config.get(key, defaults.get(key, "")))
        fields[key] = variable
        ttk.Entry(panel, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
    control = tk.BooleanVar(value=config.get("control", False))
    ttk.Checkbutton(panel, text="启用续聊、新建 Codex 任务和 ChatGPT 同步（实验性桌面连接）", variable=control).grid(row=9, column=0, columnspan=2, sticky="w", pady=10)
    ttk.Label(panel, text="Mac 连接路径需从自己的 Codex 任务获取 CODEX_APP_TOOLS_PIPE_PATH。\n只同步本地对话时，无需填写上下文任务 ID 和桌面连接路径。", wraplength=720).grid(row=10, column=0, columnspan=2, sticky="w", pady=6)
    status = tk.StringVar()
    ttk.Label(panel, textvariable=status).grid(row=11, column=0, columnspan=2, sticky="w", pady=10)
    buttons = ttk.Frame(panel)
    buttons.grid(row=12, column=0, columnspan=2, sticky="w", pady=8)
    output = tk.Text(panel, height=6, wrap="word", state="disabled")
    output.grid(row=14, column=0, columnspan=2, sticky="nsew", pady=8)
    panel.rowconfigure(14, weight=1)
    events = queue.Queue()
    busy = [False]

    def show(text):
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", text)
        output.configure(state="disabled")

    def save():
        if companion.is_running(state):
            raise ValueError("请先停止同步，再保存新的配置")
        config.update({key: variable.get().strip() for key, variable in fields.items()})
        config["control"] = control.get()
        save_config(state, config)
        return "配置已保存"

    def action(callback, background=False):
        if busy[0]:
            return
        def work():
            try:
                events.put(str(callback()))
            except Exception as error:
                events.put("操作失败：" + str(error))
            finally:
                busy[0] = False
        busy[0] = True
        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def diagnose():
        command = [sys.executable, str(companion.ROOT / "companion.py"), "doctor", "--state-dir", str(state)]
        if read_config(state).get("control"):
            command.append("--desktop")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=55,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return result.stdout or result.stderr

    def open_page():
        webbrowser.open(read_config(state)["url"])
        return "已打开手机网页入口"

    def logs():
        path = state / "sync.log"
        if not path.exists():
            path = state / "launcher.log"
        if not path.exists():
            return "暂时没有日志"
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 12000))
            return stream.read().decode("utf-8", "replace")

    for label, callback, background in [("保存配置", save, False), ("启动", lambda: companion.start(state), True),
            ("停止", lambda: companion.stop(state), False), ("连接诊断", diagnose, True),
            ("打开网页", open_page, False), ("查看日志", logs, False)]:
        ttk.Button(buttons, text=label, command=lambda cb=callback, bg=background: action(cb, bg)).pack(side="left", padx=(0, 6))
    startup = ttk.Frame(panel)
    startup.grid(row=13, column=0, columnspan=2, sticky="w")
    for label, enabled in [("开启登录自启", True), ("取消登录自启", False)]:
        ttk.Button(startup, text=label, command=lambda enable=enabled: action(lambda: companion.autostart(state, enable), True)).pack(side="left", padx=(0, 8))
    ttk.Label(panel, text="关闭此窗口后后台同步继续。电脑需要保持联网和唤醒。", wraplength=700).grid(row=15, column=0, columnspan=2, sticky="w")

    def refresh():
        while not events.empty():
            show(events.get_nowait())
        running = companion.is_running(state)
        status.set(("● 同步正在运行" if running else "○ 同步已停止") + "   ·   " + str(state))
        window.after(1000, refresh)
    refresh()
    window.mainloop()


if __name__ == "__main__":
    main()
