#!/bin/bash

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

echo ""
echo "⏳ 正在为您全自动配置网络与系统环境，请稍候..."

# 2. 安装系统依赖、Nginx 服务以及 Certbot 证书工具
apt update -y
apt install python3 python3-venv python3-pip nginx certbot python3-certbot-nginx -y

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

# 激活新反代配置，并删除 Nginx 自带的抢主页 default 文件
ln -sf $NGINX_CONF /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 6. 【核心优化】调用 Certbot 自动向官方接口申请免费 SSL 证书并魔改 Nginx 强制开启 HTTPS 加密锁
echo "🔒 正在为您向 Let's Encrypt 官方接口申请永久免费的 SSL 安全证书..."
certbot --nginx -d $WEB_DOMAIN --email $USER_EMAIL --agree-tos --no-eff-email --non-interactive

# 7. 清理可能卡死或冲突的残留端口进程，在后台挂起 Python 服务
echo "🚀 正在启动全栈后端核心引擎..."
fuser -k 8888/tcp $USER_PORT/tcp 25/tcp 2>/dev/null
pkill -9 -f app.py
nohup ./venv/bin/python app.py > mail_panel.log 2>&1 &

# 重启 Nginx 保安让证书和代理无缝生效
systemctl restart nginx

echo "=================================================="
echo "✅ LightningMail 闪电邮一键全自动化部署成功！"
echo "🌐 加密面板管理网址: https://$WEB_DOMAIN"
echo "🏷️ 系统面板名称: $USER_TITLE"
echo "🔌 软件/客户端连接 POP3 地址: $POP_HOST  端口: $USER_PORT"
echo "💡 提示: 面板域名可以放心开启 Cloudflare 黄云代理来隐藏您的真实 IP 啦！"
echo "=================================================="
