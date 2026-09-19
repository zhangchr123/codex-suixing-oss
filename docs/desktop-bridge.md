# 本机 Codex 桌面连接

本项目通过已安装 Codex 桌面应用的本地 App Tools 通道读取已有聊天、发送文字或新建 Codex 任务。它不是 OpenAI 开发者 API，不需要 API Key，也不导出账号登录凭据。

该通道属于内部协议，并非稳定的第三方 SDK。发现工具缺失或连接异常时会显示离线，不改用未公开的 ChatGPT 网络接口。普通 ChatGPT Chat 新建、附件上传暂不支持。

## 配置自己的上下文

1. 在 Codex 桌面版保留一个自己的本地任务，例如专门用于手机桥接的任务。取得其完整任务 UUID；本地 `~/.codex/sessions/` 对应日志的 `session_meta.id` 中可以找到。不要把日志整份上传或发给其他人。
2. 在电脑端“上下文任务 ID”填这个 UUID。它是本机工具调用的上下文，不是要发送消息的目标任务。代码不会自带其他用户的 UUID。
3. Windows 会尝试发现本机 `codex-browser-use-*` 命名管道，也允许显式填写连接路径。
4. macOS 填写 Codex 所提供的本机 Unix socket 路径。可以在自己的 Codex 任务中让它**只查看环境变量 `CODEX_APP_TOOLS_PIPE_PATH` 的值**；请勿读取、复制或上传 `auth.json`、Cookie 或令牌。若该变量不存在或安装版本未提供兼容通道，当前版本无法启用控制功能，不能假定某个 socket 路径必然存在。
5. 勾选启用发送，保存后点击连接诊断；或运行 `python3 companion.py doctor --desktop`。该检查只读取本机工具目录和项目列表，不创建任务或发送文字。

也可用命令行初次创建配置：

```sh
python3 companion.py init --ssh-host my-relay --url https://relay.example.com/ \
  --enable-control --context-thread YOUR_OWN_TASK_UUID --endpoint /path/to/your/socket
```

已有配置不会被 `init` 覆盖；使用图形界面修改。自启使用保存的配置，不能依赖另一个终端中临时设置的环境变量。桌面重启后若 socket 路径变化，需要更新配置。运行环境可直接提供 `CODEX_APP_TOOLS_PIPE_PATH`，但界面中的显式配置优先。

## 范围

只允许读取任务、列出本机项目、创建 Codex 任务及发送消息。接收方类型和本机归属会再次核对；手机不能指定任意本机文件作为图片。图片来自认证上传并存入私有目录。项目任务的权限沿用 Codex 当前设置，审批仍需在桌面处理。

归档上下文任务在原 Windows 部署中经过用户验证可用。删除任务、退出 Codex、电脑休眠会影响连接，应保留上下文任务。关闭随行界面不会关闭同步进程。
