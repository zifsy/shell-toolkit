#!/bin/bash
# ==============================================================================
# 服务器基础信息欢迎面板 (Server Info Dashboard) - 终极兼容版
# 修复点: 移除高阶正则，改用通用字符串判断，兼容所有 Bash 版本
# ==============================================================================

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
BASHRC_FILE="$HOME/.bashrc"
MARKER_COMMENT="# Auto-added: Server Dashboard Initialization"

# ------------------------------------------------------------------------------
# 函数 1: 自动注册
# ------------------------------------------------------------------------------
auto_register() {
    if [ ! -f "$BASHRC_FILE" ]; then
        touch "$BASHRC_FILE"
    fi
    if grep -qF "$MARKER_COMMENT" "$BASHRC_FILE"; then
        return 0
    fi
    cat >> "$BASHRC_FILE" << EOF

$MARKER_COMMENT
if [ -f "$SCRIPT_PATH" ]; then
    source "$SCRIPT_PATH"
fi
EOF
}

# ------------------------------------------------------------------------------
# 函数 2: 格式化字节数
# ------------------------------------------------------------------------------
format_bytes() {
    local bytes=$1
    if [ -z "$bytes" ] || [ "$bytes" = "0" ]; then
        echo "0 B"
        return
    fi
    # 防止非数字输入
    case $bytes in
        ''|*[!0-9]*) echo "0 B"; return ;;
    esac

    echo "$bytes" | awk '{
        units[0]="B"; units[1]="KB"; units[2]="MB"; units[3]="GB"; units[4]="TB"; units[5]="PB";
        val = $1; idx = 0;
        while (val >= 1024 && idx < 5) { val = val / 1024; idx++; }
        printf "%.1f %s", val, units[idx];
    }'
}

# ------------------------------------------------------------------------------
# 函数 3: 获取累计网络流量 (通用兼容逻辑)
# ------------------------------------------------------------------------------
get_net_traffic() {
    local rx_total=0
    local tx_total=0
    local primary_iface="unknown"
    local found_iface=0

    while read -r line; do
        # 1. 提取冒号前的部分
        local iface_raw="${line%%:*}"
        # 2. 去除首尾空格 (使用 sed 或 tr，兼容性最好)
        local iface=$(echo "$iface_raw" | tr -d ' \t')

        # 3. 【核心修复】基础有效性检查
        # 如果为空，跳过
        if [ -z "$iface" ]; then
            continue
        fi
        
        # 4. 【黑名单排除】明确跳过标题行和虚拟接口
        # 只要包含这些关键词，直接跳过
        case "$iface" in
            Inter-*|face|lo|docker*|veth*|br-*|virbr*|kube-*)
                continue
                ;;
        esac

        # 5. 提取数据部分 (冒号之后)
        local stats="${line#*:}"
        
        # 6. 解析流量 (rx_bytes=$1, tx_bytes=$9)
        # 使用 awk 安全提取
        local rx=$(echo "$stats" | awk '{print $1}')
        local tx=$(echo "$stats" | awk '{print $9}')

        # 7. 确保是数字并累加
        case $rx in ''|*[!0-9]*) rx=0 ;; esac
        case $tx in ''|*[!0-9]*) tx=0 ;; esac

        rx_total=$((rx_total + rx))
        tx_total=$((tx_total + tx))

        # 8. 记录第一个有效物理网卡
        if [ $found_iface -eq 0 ]; then
            primary_iface="$iface"
            found_iface=1
        fi
    done < /proc/net/dev

    if [ $found_iface -eq 0 ]; then
        primary_iface="lo"
    fi

    local rx_human=$(format_bytes $rx_total)
    local tx_human=$(format_bytes $tx_total)
    
    echo "${rx_human}|${tx_human}|${primary_iface}"
}

# ------------------------------------------------------------------------------
# 函数 4: 显示面板
# ------------------------------------------------------------------------------
show_server_dashboard() {
    local RED='\033[0;31m'
    local GREEN='\033[0;32m'
    local YELLOW='\033[0;33m'
    local BLUE='\033[0;34m'
    local CYAN='\033[0;36m'
    local MAGENTA='\033[0;35m'
    local BOLD='\033[1m'
    local NC='\033[0m'

    command_exists() { command -v "$1" >/dev/null 2>&1; }

    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} ${BOLD}🖥️  服务器基础信息面板 ${NC}                              ${BLUE}║${NC}"
    echo -e "${BLUE}╠════════════════════════════════════════════════════════════╣${NC}"

    # 运行时间
    local uptime_str="未知"
    if command_exists uptime; then
        local raw=$(uptime -p 2>/dev/null)
        if [ -n "$raw" ]; then
            local weeks=0 days=0 hours=0 mins=0
            [[ $raw =~ ([0-9]+)\ *week ]] && weeks=${BASH_REMATCH[1]}
            [[ $raw =~ ([0-9]+)\ *day ]] && days=${BASH_REMATCH[1]}
            [[ $raw =~ ([0-9]+)\ *hour ]] && hours=${BASH_REMATCH[1]}
            [[ $raw =~ ([0-9]+)\ *minute ]] && mins=${BASH_REMATCH[1]}
            local total_days=$((weeks * 7 + days))
            uptime_str="${total_days}天 ${hours}小时 ${mins}分"
        else
            uptime_str=$(uptime | sed 's/.*up //' | cut -d',' -f1-2)
        fi
    fi
    echo -e "${BLUE}║${NC}  🕒 运行时间 : ${GREEN}${uptime_str}${NC}"
    echo -e "${BLUE}║${NC}  🌡️  系统负载 : ${YELLOW}$(cat /proc/loadavg | awk '{print $1, $2, $3}')${NC}"

    # CPU & 内存
    local mem_info=$(free -h | grep Mem)
    local mem_total=$(echo $mem_info | awk '{print $2}')
    local mem_used=$(echo $mem_info | awk '{print $3}')
    local mem_percent=$(free | grep Mem | awk '{printf("%.1f", $3/$2 * 100.0)}')
    local cpu_cores=$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo)

    echo -e "${BLUE}║${NC}  💻 CPU 核心 : ${CYAN}${cpu_cores} Cores${NC}"
    echo -e "${BLUE}║${NC}  💾 内存使用 : ${GREEN}${mem_used}${NC} / ${CYAN}${mem_total}${NC} (${RED}${mem_percent}%${NC})"

    # 磁盘
    if command_exists df; then
        local disk_info=$(df -h / | tail -1)
        local disk_used=$(echo $disk_info | awk '{print $3}')
        local disk_avail=$(echo $disk_info | awk '{print $4}')
        local disk_percent=$(echo $disk_info | awk '{print $5}')
        echo -e "${BLUE}║${NC}  💿 磁盘 (/)  : 已用 ${YELLOW}${disk_used}${NC}, 剩余 ${GREEN}${disk_avail}${NC} (${RED}${disk_percent}${NC})"
    fi

    # 网络
    local ip_addr="N/A"
    if command_exists ip; then
        ip_addr=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
    elif command_exists hostname; then
        ip_addr=$(hostname -I | awk '{print $1}')
    fi
    
    local traffic_data=$(get_net_traffic)
    local rx_val=$(echo "$traffic_data" | cut -d'|' -f1)
    local tx_val=$(echo "$traffic_data" | cut -d'|' -f2)
    local iface_val=$(echo "$traffic_data" | cut -d'|' -f3)

    local traffic_str="${GREEN}↓ ${rx_val}${NC}  ${MAGENTA}↑ ${tx_val}${NC} (${CYAN}${iface_val}${NC})"

    echo -e "${BLUE}║${NC}  🌐 网络 IP  : ${CYAN}${ip_addr}${NC}"
    echo -e "${BLUE}║${NC}  📊 累计流量 : ${traffic_str}"

    # 连接数
    local conn_count=0
    if command_exists ss; then
        conn_count=$(ss -tun state established 2>/dev/null | wc -l)
    elif command_exists netstat; then
        conn_count=$(netstat -tun 2>/dev/null | grep ESTABLISHED | wc -l)
    fi
    echo -e "${BLUE}║${NC}  🔗 活跃连接: ${YELLOW}${conn_count}${NC} (TCP/UDP)"

    # 用户
    local user_count=$(who | wc -l)
    local current_user_list=$(who | awk '{print $1}' | sort -u | tr '\n' ' ')
    echo -e "${BLUE}║${NC}  👥 在线用户 : ${GREEN}${user_count}${NC} 人 (${CYAN}${current_user_list:-无}${NC})"

    # 重启提醒
    if [ -f /var/run/reboot-required ]; then
        echo -e "${BLUE}║${NC}  ⚠️  状态提醒 : ${RED}系统需要重启!${NC}"
    fi

    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# ==============================================================================
# 主逻辑
# ==============================================================================
if [ -w "$HOME" ] || [ -w "$BASHRC_FILE" ]; then
    auto_register
fi

if [[ $- == *i* ]]; then
    show_server_dashboard
else
    if [ "$(realpath "$0" 2>/dev/null)" == "$(realpath "$SCRIPT_PATH" 2>/dev/null)" ]; then
        show_server_dashboard
    fi
fi