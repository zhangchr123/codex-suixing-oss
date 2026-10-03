# 本机发送与桌面连接

Codex 任务使用官方 CLI 的 app-server，普通 ChatGPT 聊天使用已登录桌面的本地 App Tools 通道。模型调用留在电脑，不需要开发者 API Key，也不向服务器上传 Cookie、登录文件或账户令牌。

## Codex

安装 CLI、完成自己的登录，在随行界面勾选启用发送即可。原生新建、续聊使用 thread/start、thread/resume、turn/start，图片作为独立 localImage 输入。选项来自真实 model/list；手机可以选择模型及思考强度、处理原生确认和补充问题。

桌面持有某个对话的写入权时，第二个 CLI 可能无法接管。此时显示失败，不借另一项任务转发，也不把“入队成功”当作开始执行。手机新建任务可直接运行。每轮结束 2 秒后关闭对应引擎，便于桌面继续使用同一份本地历史。

运行中的 CLI 引擎由独立本地服务持有，同步进程重启不杀模型任务。图片、回执和回环接口令牌放在配置目录内；接口仅监听本机回环地址，手机只能访问认证后的服务器队列。结果不确定时不自动重发。

## 普通 ChatGPT 聊天

1. 在自己的 Codex 桌面保留一个本人可访问的任务，并把 UUID 填入可选“ChatGPT 桌面上下文 ID”。它只用于普通 ChatGPT 和项目读取，Codex 原生发送不依赖它。
2. Windows 自动发现当前用户桌面管道；macOS 配置实际存在的 App Tools Unix socket，可从自己的运行环境 CODEX_APP_TOOLS_PIPE_PATH 核对。不要读取或复制 auth.json、Cookie 或令牌。
3. 用 python3 companion.py doctor --desktop 检查本机目录与连接。诊断只读，不发送消息。

桌面更新可能改变内部协议。普通 ChatGPT Chat 目前仅支持查看和文字续聊；新建、原有附件及图片仍在电脑操作。关闭随行界面后同步继续。

## 私有目录与迁移

Windows 默认使用程序目录下 .state，macOS 和 Linux 默认使用用户数据目录，也可显式指定 --state-dir。该目录包含照片、预览、两层防重复回执、配置、日志和回环令牌，不进入 Git。

移动程序时保留整个状态目录，再重建自启，更新 SSH 的绝对主机密钥路径。不要同时运行两份镜像。CLI 登录与 Codex 对话日志仍由官方 CLI 管理，本项目不会自行迁移它们。
