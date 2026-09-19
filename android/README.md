# Android App（自行构建）

Android 9+，系统 WebView。首次绑定在自己的已登录网页授权，随后使用系统生物识别或锁屏凭据解锁。手机令牌使用 Android Keystore 加密保存，不将网页密码编入 APK。

公开源码不附带任何人的 APK、证书或签名密钥。APK 会包含你配置的服务器地址；如果你将 APK 分享给别人，他们能看到这个地址，但仍需授权才能访问对话。

## 构建

准备 Python 3.11+、JDK 17、标准 Android SDK 的 `platforms;android-35` 和 `build-tools;35.0.0`。设置 `ANDROID_HOME`，或使用 `--sdk` 指定 SDK 目录。Windows、macOS、Linux 使用相应 SDK 中的工具。

创建自己的私有 `connection.json`，内容至少含 `url` 字段，必须是纯 HTTPS 地址，例如 `https://relay.example.com/`。也可使用电脑端已保存的完整配置：

```sh
python3 android/build.py --connection /path/to/private/connection.json
```

这将使用系统可信 CA。服务器使用自签名证书时，增加参数：

```sh
python3 android/build.py --connection /path/to/private/connection.json \
  --certificate /path/to/public/server.crt
```

只提供公开证书，不能提供私钥。构建后的证书资源写在忽略的构建目录，不修改源码。默认输出 `downloads/codex-suixing.apk` 和 SHA-256 文件；可使用 `--output` 指定路径。

构建目录为源码旁 `.state/android-build/`。首次构建会在 `.state/android-signing/` 生成私人签名密钥及其密码。妥善备份并保持私密；后续覆盖更新必须使用同一签名并提高 `--version-code`，例如 `--version-code 2 --version-name 1.0.1`。

开源版使用通用包名 `org.codexsuixing.app`。它与早期私人部署的 App 为不同应用，需要重新绑定。

## 下载与绑定

用 `deploy_cloud.py --apk downloads/codex-suixing.apk` 上传到自己的服务器，手机打开 `/android` 下载，然后在网页按提示授权 App 绑定。App 只能访问构建时配置的 HTTPS 入口。更换入口或所信任的自签名证书时，重新构建并安装。

支持 Codex 图片选择和网页 Markdown；普通 ChatGPT 聊天当前仅支持文字。App 锁屏后再次打开需解锁；电脑和同步程序离线时只能查看上次同步内容。
