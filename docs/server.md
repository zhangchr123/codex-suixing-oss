# 部署自己的服务器

以下示例适用于带 systemd 的 Linux，Python 3.11+、OpenSSH；域名 `relay.example.com` 和 SSH 别名 `my-relay` 均为占位示例。服务按单个用户设计。

## 1. 专用账号与 SSH

由管理员创建普通用户 `codexsync`，家目录 `/home/codexsync`，并给其 `~/.ssh/authorized_keys` 加入电脑专用公钥。私钥只留在电脑，不传给服务器，不提交 Git。电脑 `~/.ssh/config` 示例：

```sshconfig
Host my-relay
    HostName relay.example.com
    User codexsync
    IdentityFile ~/.ssh/codex_suixing
    IdentitiesOnly yes
    StrictHostKeyChecking yes
```

通过可信的服务器控制台核对 SSH 主机公钥指纹，再登记到电脑的 `known_hosts`。确认 `ssh my-relay 'python3 --version'` 可在不输入密码的情况下执行。不要用关闭主机校验的方式绕过问题。

## 2. 上传应用

在电脑源码目录初次配置：

```sh
python3 companion.py init --ssh-host my-relay --url https://relay.example.com/
python3 deploy_cloud.py
ssh my-relay 'python3 ~/.local/share/codex-viewer/viewer.py init'
```

上传脚本只复制运行所需源码和静态资源，目标固定为远端 `~/.local/share/codex-viewer`。服务器密码由 `init` 随机生成，存在该目录 `.state/password.txt` 中；重复初始化不覆盖密码。可通过 SSH 在自己的终端读取，用于首次网页登录。

## 3. HTTPS 和服务

为自己的域名申请可信 HTTPS 证书，将证书链和私钥分别安装为：

```text
/home/codexsync/.local/share/codex-viewer/.state/server.crt
/home/codexsync/.local/share/codex-viewer/.state/server.key
```

目录仅本人可访问，私钥仅 `codexsync` 可读。可使用自己管理的自签名证书，但手机浏览器需要信任它；Android 构建时则必须提供对应公开证书。仓库里的测试证书仅供本机测试，绝不可部署。

管理员把 [codex-viewer.service](../deployment/codex-viewer.service) 安装到 `/etc/systemd/system/`，按实际账号、家目录、Python 路径修改，然后：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now codex-viewer.service
sudo systemctl status codex-viewer.service --no-pager
```

示例使用非 root 用户，通过有限的 `CAP_NET_BIND_SERVICE` 监听 443。在服务器防火墙和云安全组放行需要的 HTTPS 443 和管理用 SSH 端口；不对外开放任何桌面管道或调试端口。IPv6 部署可将服务参数 `--host 0.0.0.0` 改为 `--host ::`，并配置相应 DNS 与网络规则。

访问 `https://relay.example.com/health` 应返回 `ok`，手机打开主页应显示登录页面。回到电脑启动同步，再在手机登录查看任务。

## 更新、备份和迁移

```sh
python3 deploy_cloud.py
```

静态页面更新立即生效。Python 文件更新后管理员重启服务；如有需要，可单独授予 `codexsync` 仅执行 `systemctl restart codex-viewer.service` 的 sudo 权限，之后才使用 `deploy_cloud.py --restart`，无需完整 sudo 权限。

私下备份服务器 `.state/`（密码、快照、队列、设备令牌哈希、图片和 TLS 密钥），以及电脑端数据目录中的配置、图片和发送回执。Android 签名密钥也应私下备份。Git 开源仓库不能代替这些私有备份。

网页会话在服务器重启后失效，需要重新登录；已绑定 App 可以重新解锁换取会话。同步程序修改后需要停止再启动。不要把多台电脑同时接到同一服务器数据目录。
