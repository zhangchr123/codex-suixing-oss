# macOS 电脑端

## 安装

1. 安装 [Python.org 的 Python 3.11 或更新版本](https://www.python.org/downloads/macos/)（含 Tk 图形界面），以及 [Node.js](https://nodejs.org/) 20+。只同步日志时不需要 Node。
2. 在 Release 下载 macOS 压缩包，解压，将 `Codex Suixing.app` 放在固定目录，例如 `~/Applications/`。它是源码启动器，需要上述运行环境，不是包含 Python 的独立安装包，也未使用 Apple 开发者证书签名。请按 macOS 的提示决定是否允许自己下载的程序运行；不需要关闭系统整体安全检查。
3. 也可从源码目录运行 `python3 desktop.py`，或首次执行 `chmod +x 'Start macOS.command'` 后双击启动。Git 克隆会保留可执行位。
4. 填写 SSH 别名、服务器 HTTPS 地址并保存，点击启动。服务器设置见 [部署文档](server.md)。

苹果芯片和 Intel 使用相同 Python 源码；实际能否发送消息取决于当前 Codex 桌面版是否提供兼容的本地通道。

## 续聊与新建任务

按 [桌面桥说明](desktop-bridge.md) 填自己的任务 ID、Unix socket 路径，勾选启用发送，然后运行连接诊断。没有该连接时仍能同步 Codex 日志，不能发送或读取云端 ChatGPT 聊天。

Node 自动查找失败时，在终端运行 `command -v node`，将完整路径填入界面。桌面启动器会加入 `/opt/homebrew/bin` 和 `/usr/local/bin`；通过 nvm 安装的 Node 通常需要显式配置。Codex 日志目录默认是 `~/.codex`，也支持 `CODEX_HOME`。

## 后台和自启

关闭界面不会停止同步。点击停止会等当前传输结束并释放锁，不会根据旧 PID 误杀其他程序。

登录自启使用当前用户的 `~/Library/LaunchAgents/org.codexsuixing.companion.plist`，无需 root。可在界面开启 / 取消，或运行 `python3 companion.py autostart` / `autostart --disable`。配置路径中的空格会作为独立参数传给 Python。[Apple 的 LaunchAgent 说明](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)解释了该机制。

默认数据在 `~/Library/Application Support/CodexSuixing/`，其中 `sync.log` 是同步日志，`launcher.log` 记录启动错误。取消自启可能停止由 LaunchAgent 启动的进程；手动启动的进程请再点停止。

卸载时先停止同步、取消自启，再移除程序。数据目录单独保留，是否删除由你决定。迁移时保留 `delivery-receipts.json`，它用于防止重复发送。

## 验证边界

自动测试覆盖 macOS 文件锁、启停重启、带空格路径的 LaunchAgent 配置和真实 Unix socket 上的模拟 App Tools 请求。自动测试不登录 ChatGPT，也不代表每个 Codex macOS 版本均兼容。已登录 Codex 的实机端到端发送，以及指纹 App 的设备表现，仍需要对应设备验证。
