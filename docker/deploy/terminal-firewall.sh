#!/usr/bin/env bash
# Chặn terminal của AI kết nối tới chính máy chủ (host).
#
# Mạng ncs-terminal-net là "internal": không có đường ra ngoài. Nhưng host vẫn có địa chỉ 172.31.250.1 trên bridge
# đó, nên dịch vụ nào trên host mở ở mọi địa chỉ (SSH, cổng NCS AI 8000…) vẫn nhận được kết nối từ terminal.
# Luật dưới đây chặn mọi gói từ dải terminal đi VÀO host (chuỗi INPUT). Lưu lượng giữa các container trên cùng
# bridge (terminal -> egress-proxy, broker -> terminal) không đi qua INPUT nên không bị ảnh hưởng.
#
#   sudo ./terminal-firewall.sh          thêm luật (chạy lại không bị trùng)
#   sudo ./terminal-firewall.sh remove   gỡ luật
#
# Luật iptables mất khi khởi động lại máy: thêm lệnh này vào cron @reboot hoặc dùng iptables-persistent.
# Kiểm tra: docker exec ncs-terminal-broker python -m broker.smoke   (mục "host port ... not reachable")
set -euo pipefail

SUBNET=${TERMINAL_SUBNET:-172.31.250.0/24}
RULE=(INPUT -s "$SUBNET" -m comment --comment ncs-terminal-no-host -j DROP)

if [ "${1:-add}" = remove ]; then
    while iptables -C "${RULE[@]}" 2>/dev/null; do iptables -D "${RULE[@]}"; done
    echo "Đã gỡ luật chặn $SUBNET -> host"
    exit 0
fi

if iptables -C "${RULE[@]}" 2>/dev/null; then
    echo "Luật đã có: $SUBNET không vào được host"
else
    iptables -I "${RULE[@]}"
    echo "Đã thêm: $SUBNET không vào được host"
fi
if command -v ip6tables >/dev/null 2>&1; then
    echo "(IPv6 không bật cho ncs-terminal-net nên không cần luật ip6tables)"
fi
