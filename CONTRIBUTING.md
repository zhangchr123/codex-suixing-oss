# 贡献

使用 Python 3.11+ 和 Node.js 20+，无需安装 Python 第三方包。运行 README 中的测试后提交 PR。涉及桌面桥的变更，请用隔离的假管道 / socket 编写回归，不对真实账号发送测试消息。

优先欢迎 macOS 实机兼容性反馈、Codex 版本适配和文档改进。报告中可以说明系统与 Codex 版本，但请去除真实任务 ID、socket 路径、服务器地址、对话内容和凭据。

提交前运行 `python3 scripts/audit_source.py` 并查看 `git diff --cached`。发布包由 Git 跟踪的源文件生成；不要上传自己的配置、证书、APK 或签名材料。第三方文件需保留许可。
