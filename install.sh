#!/bin/bash
set -e

# 确保脚本以 root 权限运行
if [ "$EUID" -ne 0 ]; then
  echo "❌ 请使用 root 用户或使用 sudo 运行此脚本！"
  exit 1
fi

echo "=================================================="
echo "    ⚡ LightningMail 闪电邮 SaaS 级一键全自动部署"
echo "=================================================="
echo ""

# 1. 交互式询问用户的个性化配置
read -p "👉 请输入您的面板名称 (直接回车默认使用 '闪电邮'): " USER_TITLE
USER_TITLE=${USER_TITLE:-"闪电邮"}

read -p "👉 请输入您的【Web 面板访问域名】(用于网页访问，可开CF黄云，例如 panel.abc.com): " WEB_DOMAIN
if [ -z "$WEB_DOMAIN" ]; then
  echo "❌ Web 面板访问域名不能为空！"
  exit 1
fi

read -p "👉 请输入您的【POP3 外部连接地址】(提供给接码软件或客户端连接，不可开CF黄云，直接回车则默认使用上述面板域名): " POP_HOST
POP_HOST=${POP_HOST:-"$WEB_DOMAIN"}

read -p "👉 请输入您要使用的 POP3 运行端口 (直接回车默认使用 110): " USER_PORT
USER_PORT=${USER_PORT:-110}

read -p "👉 请输入您的管理员邮箱 (用于全自动申请 Let's Encrypt SSL 证书): " USER_EMAIL
if [ -z "$USER_EMAIL" ]; then
  echo "❌ 邮箱不能为空，申请安全证书必须提供通知邮箱！"
  exit 1
fi

read -p "👉 是否同时安装独立登录邮箱面板？(y/N): " INSTALL_LOGIN_PANEL
INSTALL_LOGIN_PANEL=${INSTALL_LOGIN_PANEL:-N}

if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  read -p "👉 请输入【登录邮箱面板访问域名】(例如 login.abc.com): " LOGIN_DOMAIN
  if [ -z "$LOGIN_DOMAIN" ]; then
    echo "❌ 选择安装登录面板时，登录面板域名不能为空！"
    exit 1
  fi

  read -p "👉 请输入登录面板管理员账号 (直接回车默认 admin): " LOGIN_ADMIN_USER
  LOGIN_ADMIN_USER=${LOGIN_ADMIN_USER:-admin}

  read -s -p "👉 请输入登录面板管理员密码 (直接回车默认 ChangeMe123!): " LOGIN_ADMIN_PASS
  echo ""
  LOGIN_ADMIN_PASS=${LOGIN_ADMIN_PASS:-ChangeMe123!}

  read -p "👉 请输入登录面板默认允许邮箱后缀 (例如 edu.zitw.de，直接回车默认 $POP_HOST): " LOGIN_ALLOWED_DOMAIN
  LOGIN_ALLOWED_DOMAIN=${LOGIN_ALLOWED_DOMAIN:-"$POP_HOST"}

  read -p "👉 请输入登录面板内部运行端口 (直接回车默认 8899): " LOGIN_INTERNAL_PORT
  LOGIN_INTERNAL_PORT=${LOGIN_INTERNAL_PORT:-8899}
fi

echo ""
echo "⏳ 正在为您全自动配置网络与系统环境，请稍候..."

# 2. 检查并安装系统依赖、Nginx 服务以及 Certbot 证书工具
if ! command -v apt-get >/dev/null 2>&1; then
  echo "❌ 当前一键安装脚本仅支持 Debian/Ubuntu（需要 apt-get）。"
  echo "   建议使用 Debian 12 或 Ubuntu 22.04/24.04。"
  exit 1
fi

REQUIRED_PACKAGES=(
  git
  ca-certificates
  curl
  python3
  python3-venv
  python3-pip
  nginx
  certbot
  python3-certbot-nginx
  psmisc
)
MISSING_PACKAGES=()

echo "🔎 正在检查必要组件..."
for package in "${REQUIRED_PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
    MISSING_PACKAGES+=("$package")
  fi
done

if [ "${#MISSING_PACKAGES[@]}" -gt 0 ]; then
  echo "📦 正在安装缺失组件: ${MISSING_PACKAGES[*]}"
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING_PACKAGES[@]}"
else
  echo "✅ 必要组件均已安装。"
fi

REQUIRED_COMMANDS=(git python3 nginx certbot fuser systemctl)
for command_name in "${REQUIRED_COMMANDS[@]}"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "❌ 组件检查失败，找不到命令: $command_name"
    exit 1
  fi
done

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "❌ Python 版本过低，当前依赖需要 Python 3.10 或更高版本。"
  python3 --version
  echo "   建议使用 Debian 12 或 Ubuntu 22.04/24.04。"
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "❌ Python 虚拟环境不可用，请检查 python3-venv。"
  exit 1
fi

echo "✅ 系统环境检查通过。"

# 释放 25 端口，避免系统自带 MTA 抢占收信端口
systemctl disable --now exim4 2>/dev/null || true
systemctl disable --now postfix 2>/dev/null || true

# 3. 【核心优化】将参数精准写入全局环境变量文件，100%告别源码sed篡改
CAT_ENV_FILE="/etc/lightningmail.env"
cat << EOF > $CAT_ENV_FILE
LM_TITLE="$USER_TITLE"
LM_WEB_DOMAIN="$WEB_DOMAIN"
LM_POP_HOST="$POP_HOST"
LM_PORT=$USER_PORT
EOF

# 4. 创建 Python 隔离虚拟环境并安装核心依赖库
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install fastapi uvicorn aiosmtpd pydantic

if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  echo "🔐 正在配置独立登录邮箱面板..."
  python3 -m venv mail-login-panel/venv
  mail-login-panel/venv/bin/pip install --upgrade pip
  mail-login-panel/venv/bin/pip install -r mail-login-panel/requirements.txt

  cat << EOF > mail-login-panel/.env
APP_HOST=127.0.0.1
APP_PORT=$LOGIN_INTERNAL_PORT

POP_HOST=127.0.0.1
POP_PORT=$USER_PORT
POP_SSL=false
POP_TIMEOUT=10

SESSION_EXPIRE_MINUTES=1440
DEFAULT_ALLOWED_DOMAIN=$LOGIN_ALLOWED_DOMAIN

ADMIN_USERNAME=$LOGIN_ADMIN_USER
ADMIN_PASSWORD=$LOGIN_ADMIN_PASS
EOF
fi

# 5. 【核心优化】全自动配置 Nginx 反向代理（守住 80 端口，自动转发至内部 8888 房间）
NGINX_CONF="/etc/nginx/sites-available/mail_panel"
cat << EOF > $NGINX_CONF
server {
    listen 80;
    server_name $WEB_DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # 完美支持 Web-Socket 前后端长连接，确保前端 1 秒无感无闪烁同步
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  LOGIN_NGINX_CONF="/etc/nginx/sites-available/mail_login_panel"
  cat << EOF > $LOGIN_NGINX_CONF
server {
    listen 80;
    server_name $LOGIN_DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$LOGIN_INTERNAL_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
fi

# 激活新反代配置，并删除 Nginx 自带的抢主页 default 文件
ln -sf $NGINX_CONF /etc/nginx/sites-enabled/
if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  ln -sf $LOGIN_NGINX_CONF /etc/nginx/sites-enabled/
fi
rm -f /etc/nginx/sites-enabled/default

# 6. 【核心优化】调用 Certbot 自动向官方接口申请免费 SSL 证书并魔改 Nginx 强制开启 HTTPS 加密锁
echo "🔒 正在为您向 Let's Encrypt 官方接口申请永久免费的 SSL 安全证书..."
certbot --nginx -d $WEB_DOMAIN --email $USER_EMAIL --agree-tos --no-eff-email --non-interactive
if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  certbot --nginx -d $LOGIN_DOMAIN --email $USER_EMAIL --agree-tos --no-eff-email --non-interactive
fi

# 7. 配置 systemd 开机自启服务
echo "🚀 正在启动全栈后端核心引擎..."
fuser -k 8888/tcp $USER_PORT/tcp 25/tcp 2>/dev/null || true
pkill -9 -f app.py 2>/dev/null || true

INSTALL_DIR=$(pwd)
cat << EOF > /etc/systemd/system/lightningmail.service
[Unit]
Description=LightningMail TempMail Panel
After=network.target nginx.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$CAT_ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/app.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF

if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  fuser -k $LOGIN_INTERNAL_PORT/tcp 2>/dev/null || true
  cat << EOF > /etc/systemd/system/mail-login-panel.service
[Unit]
Description=Mail Login Panel
After=network.target lightningmail.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR/mail-login-panel
ExecStart=$INSTALL_DIR/mail-login-panel/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port $LOGIN_INTERNAL_PORT
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload
systemctl enable --now lightningmail
if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  systemctl enable --now mail-login-panel
fi

# 重启 Nginx 保安让证书和代理无缝生效
nginx -t
systemctl restart nginx

echo "=================================================="
echo "✅ LightningMail 闪电邮一键全自动化部署成功！"
echo "🌐 加密面板管理网址: https://$WEB_DOMAIN"
echo "🏷️ 系统面板名称: $USER_TITLE"
echo "🔌 软件/客户端连接 POP3 地址: $POP_HOST  端口: $USER_PORT"
if [[ "$INSTALL_LOGIN_PANEL" =~ ^[Yy]$ ]]; then
  echo "🔐 独立登录邮箱面板: https://$LOGIN_DOMAIN"
  echo "👤 登录面板管理员账号: $LOGIN_ADMIN_USER"
fi
echo "💡 提示: 面板域名可以放心开启 Cloudflare 黄云代理来隐藏您的真实 IP 啦！"
echo "=================================================="
