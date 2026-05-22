#!/bin/bash

echo "=================================================="
echo "      ⚡ 极速接码邮局 (TempMail) 一键部署脚本"
echo "=================================================="
echo ""

# 1. 交互式询问用户的个性化配置
read -p "👉 请输入您的面板名称 (直接回车默认使用 '闪电邮'): " USER_TITLE
USER_TITLE=${USER_TITLE:-"闪电邮"}

read -p "👉 请输入您的 POP3 服务器域名 (例如 mail.abc.com): " USER_DOMAIN
USER_DOMAIN=${USER_DOMAIN:-"mail.abc.com"}

read -p "👉 请输入您要使用的 POP3 端口 (直接回车默认使用 110): " USER_PORT
USER_PORT=${USER_PORT:-110}

echo ""
echo "⏳ 正在为您配置环境，请稍候..."

# 2. 安装系统依赖
sudo apt update -y
sudo apt install python3 python3-venv python3-pip -y

# 3. 创建虚拟环境并安装 Python 库
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn aiosmtpd pydantic

# 4. 动态替换代码中的占位符
sed -i "s/{{REPLACE_TITLE}}/$USER_TITLE/g" index.html
sed -i "s/{{REPLACE_DOMAIN}}/$USER_DOMAIN/g" index.html
sed -i "s/{{REPLACE_PORT}}/$USER_PORT/g" index.html
sed -i "s/{{REPLACE_PORT}}/$USER_PORT/g" app.py

# 5. 清理旧进程并启动
echo "🚀 正在启动服务..."
sudo fuser -k 8888/tcp $USER_PORT/tcp 25/tcp 2>/dev/null
nohup sudo ./venv/bin/python app.py > mail_panel.log 2>&1 &

echo "=================================================="
echo "✅ 部署成功！"
echo "🌐 请在浏览器访问: http://您的服务器IP:8888"
echo "🏷️ 面板名称: $USER_TITLE"
echo "🔗 绑定域名: $USER_DOMAIN"
echo "🔌 POP3 端口: $USER_PORT"
echo "=================================================="