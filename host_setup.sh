#!/bin/sh
# Enable CUBIC, Reno, and BBR on the host kernel shared with the container.
# Run this on the Ubuntu 22.04 VM (or any Linux/WSL2 host), not inside the image.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run on the host as root: sudo ./host_setup.sh" >&2
    exit 1
fi

echo "Host kernel: $(uname -r)"
echo "uname: $(uname -a)"

for mod in tcp_reno tcp_cubic tcp_bbr; do
    if modprobe "$mod" 2>/dev/null; then
        echo "loaded $mod"
    else
        echo "note: modprobe $mod did not load a module (built-in kernels do this for reno and cubic)"
    fi
done

sysctl -w net.ipv4.tcp_allowed_congestion_control="reno cubic bbr"

mkdir -p /etc/modules-load.d /etc/sysctl.d
printf '%s\n' tcp_bbr > /etc/modules-load.d/tcp_bbr.conf
printf '%s\n' 'net.ipv4.tcp_allowed_congestion_control=reno cubic bbr' > /etc/sysctl.d/99-tcp-cc.conf

available=$(sysctl -n net.ipv4.tcp_available_congestion_control)
allowed=$(sysctl -n net.ipv4.tcp_allowed_congestion_control)
echo "tcp_available_congestion_control=$available"
echo "tcp_allowed_congestion_control=$allowed"
echo "tcp_congestion_control=$(sysctl -n net.ipv4.tcp_congestion_control)"

missing=0
for algo in reno cubic bbr; do
    case " $available " in
        *" $algo "*) ;;
        *)
            echo "error: '$algo' is not in tcp_available_congestion_control" >&2
            missing=1
            ;;
    esac
done

if [ "$missing" -ne 0 ]; then
    echo "BBR is CONFIG_TCP_CONG_BBR. On Ubuntu: sudo apt-get install linux-modules-extra-\$(uname -r) and reboot if the module is missing." >&2
    exit 1
fi

echo "Host is ready. Build and run the container with --network host (see README.md)."
