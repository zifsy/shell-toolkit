# Shell Toolkit

一个实用的服务器信息监控工具集合。

## 功能介绍

### server_dashboard.sh
服务器基础信息欢迎面板，在登录时自动显示系统状态。

**显示信息包括：**
- 🕒 系统运行时间
- 🌡️ 系统负载
- 💻 CPU 核心数
- 💾 内存使用情况
- 💿 磁盘使用情况
- 🌐 网络 IP 地址
- 📊 累计网络流量（上下行）
- 🔗 活跃连接数
- 👥 在线用户
- ⚠️ 系统重启提醒

## 使用方法

### 自动注册（推荐）
脚本会自动检测并注册到 `~/.bashrc`，登录时自动显示面板：

```bash
# 直接执行即可自动注册
curl -sSL https://raw.githubusercontent.com/zifsy/shell-toolkit/refs/heads/main/server_dashboard.sh -o server_dashboard.sh && chmod +x server_dashboard.sh && ./server_dashboard.sh
```

### 手动运行
```bash
# 显示服务器信息面板
bash server_dashboard.sh
```

### 取消自动显示
编辑 `~/.bashrc` 文件，删除包含 `# Auto-added: Server Dashboard Initialization` 的代码块。

## 系统要求

- Linux 系统
- Bash 环境
- 标准工具：`awk`, `free`, `df`, `uptime`, `who`, `ss` 或 `netstat`

## 兼容性

- 兼容所有 Bash 版本
- 支持常见的 Linux 发行版（Ubuntu、CentOS、Debian 等）
- 自动适配不同的网络接口命名
