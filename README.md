# ⚡ LightningMail - 闪电邮极速接码邮局

这是一个专为“批量接码、自动化注册、薅羊毛”等高频场景打造的 SaaS 级极轻量化邮局面板。无需繁琐的后端配置，自带 Nginx 反向代理与全自动 Let's Encrypt HTTPS 证书申请，一键部署，即刻开箱可用。

## ✨ 核心特性

- 🛡️ **生产级网络架构**：内置 Nginx 自动反代与 SSL 证书全自动申请，彻底告别丑陋的端口尾巴。
- ☁️ **Cloudflare 完美兼容**：独创“面板域名”与“POP3 节点”分离架构，网页尽情开启 CDN 代理（黄云），邮件拉取直连穿透（灰云）。
- 🌍 **零预设全域接收 (Catch-all)**：安装时无需绑定域名，所有域名后缀全权在网页端 UI 动态添加，支持多域名矩阵同时接码！
- 🚀 **极简部署，告别依赖**：告别容易报错的源码占位符替换，采用高级全局环境变量注入，代码 100% 纯净高内聚。
- 📦 **独立阅读沙盒**：底层拦截恶意脚本，完美安全渲染各类复杂的商业图文 HTML 邮件。
- 📎 **附件下载**：支持从原始邮件中提取 PDF 等附件，例如 Stripe 发票和收据。
- ⚡ **无感极速刷新**：WebSocket 级顺滑体验，1秒级轮询拉取，页面无闪烁。
- ☁️ **多用户云端同步**：邮箱归属账号并同步到服务器，换设备登录后仍可继续使用。
- 🔐 **注册与激活码**：游客不能生成邮箱；无激活码也能注册登录，但每天最多生成 10 个，激活后解除每日数量限制。
- 🧱 **服务端严格配额**：创建与同步共用原子化每日配额，无法通过刷新页面、换设备或直接调用接口绕过。

## 📦 可选组件

`mail-login-panel/` 中包含一个独立的邮箱登录面板，可以让用户使用 POP3 邮箱账号登录并查看邮件。它默认作为独立服务运行，建议通过 Nginx 反代到单独域名。

---

## 🚀 一键极速安装指南

推荐使用一台干净的 Linux 服务器（Debian 12+ 或 Ubuntu 22.04/24.04），请以 `root` 权限分步执行以下命令：

### 1. 安装 Git 并克隆代码仓库
```bash
apt update
apt install -y git
git clone https://github.com/wcfbxw/TempMail-Panel.git
cd TempMail-Panel
```
### 启动全自动安装脚本（无需执行权限）
```bash
bash install.sh
```

安装过程中脚本会询问是否同时安装 `mail-login-panel` 独立登录邮箱面板。选择 `y` 后会继续要求填写：

- 登录邮箱面板访问域名，例如 `login.example.com`
- 登录面板管理员账号和密码
- 登录面板默认允许的邮箱后缀，例如 `edu.zitw.de`
- 登录面板内部运行端口，默认 `8899`

脚本会先检查并安装 Python、虚拟环境、Nginx、Certbot、`fuser` 等必要组件，然后自动创建 `mail-login-panel` 的虚拟环境、`.env` 配置、systemd 自启服务、Nginx 反向代理和 HTTPS 证书。

如果 DNS、80 端口或 443 端口暂未就绪，证书申请失败不会再中断整个安装；面板会先通过 HTTP 启动，修正网络后可重新运行 Certbot。

## 🔑 激活码管理

安装脚本会直接在所安装服务器的数据库中生成首个一次性激活码，并且只显示一次。后续请在服务器的项目目录中管理激活码：

```bash
cd /root/TempMail-Panel

# 生成一个默认的一次性、永久有效激活码
./venv/bin/python manage_activation_codes.py generate

# 批量生成 5 个、30 天内有效的激活码
./venv/bin/python manage_activation_codes.py generate --count 5 --days 30

# 生成一个最多可激活 10 个账号的激活码
./venv/bin/python manage_activation_codes.py generate --uses 10

# 查看状态（数据库不保存完整激活码，只显示末四位）
./venv/bin/python manage_activation_codes.py list

# 吊销尚未使用完的激活码
./venv/bin/python manage_activation_codes.py revoke LM-XXXX-XXXX-XXXX-XXXX
```

用户可以在注册时不填写激活码并正常登录；这种基础账号每天最多生成 10 个新邮箱。用户以后取得有效激活码，也可以登录后点击“激活”解除每日生成数量限制。
