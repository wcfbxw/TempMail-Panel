# ⚡ LightningMail - 极速接码邮局面板

这是一个专为“批量接码、自动化注册、薅羊毛”场景打造的轻量化无密邮局面板。拥有极简 SaaS 级 UI、纯文本极速提取引擎、完美渲染 HTML 商业邮件等特性。

## ✨ 核心特性
- **Catch-all 全域接收**：账号前缀随便填，无需提前创建。
- **自定义安装**：一键脚本，可自定义面板名称、POP3 域名和端口。
- **独立阅读沙盒**：完美解析并渲染复杂的商业图文 HTML 邮件。
- **无感极速刷新**：1秒级轮询拉取，页面无闪烁。
- **多用户云同步**：支持游客模式与账号登录，数据永久云端同步。

## 🚀 一键安装指南

在一台干净的 Linux (推荐 Ubuntu 20.04+) 服务器上依次执行以下命令：

1. 克隆代码仓库：
```bash
git clone https://github.com/wcfbxw/TempMail-Panel.git
cd TempMail-Panel`
chmod +x install.sh`
./install.sh`
