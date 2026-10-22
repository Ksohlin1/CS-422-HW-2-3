# CS 422 HW 2&3

Python iperf3 client and congestion-control comparison (CUBIC, Reno, BBR). The container supplies Ubuntu 24.04 user space. TCP behavior and `TCP_INFO` come from the host kernel, so run it on Ubuntu 22.04+ or WSL2.

Each run writes `host_environment.json` in its output directory with the host kernel (`uname -r`), the user-space OS, and the TCP congestion-control sysctls actually visible to the process.

## Host configuration (Ubuntu 22.04 VM)

Install Docker, then load BBR and allow the three algorithms. Reno and CUBIC are built into the Ubuntu kernel. BBR is the `tcp_bbr` module. These sysctls live in the host network namespace, so the container must be started with `--network host`.

```bash
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# log out and back in so the docker group applies

sudo ./host_setup.sh
```

`host_setup.sh` loads `tcp_bbr`, sets `net.ipv4.tcp_allowed_congestion_control=reno cubic bbr`, and persists both across reboot (`/etc/modules-load.d/tcp_bbr.conf`, `/etc/sysctl.d/99-tcp-cc.conf`). It prints the host kernel and refuses to continue if any of the three algorithms is missing. If `tcp_bbr` will not load:

```bash
sudo apt-get install -y "linux-modules-extra-$(uname -r)"
sudo reboot
sudo ./host_setup.sh
```

## Build and run

From this directory on the VM:

```bash
docker build -t cs422-hw2 .
```

Smoke test, one server for 5 seconds, using the host kernel:

```bash
mkdir -p results
docker run --rm --network host \
  -v "$PWD/results:/app/results" \
  cs422-hw2 \
  listed_iperf3_servers.json -n 1 --duration 5 --max-attempts 5 \
  --output-dir results/smoke
```

The image runs 5 destinations for 30 seconds, 3 repetitions, algorithms `cubic reno bbr`, sampled every 0.2 seconds. That is 45 measured transfers plus short probes, about half an hour. On the VM without Docker the same run is:

```bash
python3 main.py listed_iperf3_servers.json --compare -n 5 --duration 30 --repetitions 3 --output-dir results/q3_final
```

`docker run` with no extra arguments runs that command. Mount `results/` so the files survive the container. This writes to `results/q3_final` and replaces `config.json`, `run_log.csv`, and the per-algorithm outputs already in that directory.

```bash
docker run --rm --network host \
  -v "$PWD/results:/app/results" \
  cs422-hw2
```

Rebuild the Q3 plots from a finished run:

```bash
docker run --rm --network host -v "$PWD/results:/app/results" cs422-hw2 \
  --replot results/q3_final --representative 170.83.216.19:5202
```

`--network host` is required. It avoids an extra NAT hop on the measured path, and it makes the container see the host's allowed congestion-control list. A bridge network plus `--cap-add NET_ADMIN` can select algorithms only after the host has loaded the modules, and the measurements then include Docker NAT.

## Command-line options

`main.py` accepts a JSON or CSV server list (the iperf3serverlist.net export). `listed_iperf3_servers.json` is included.

| Option | Default | Meaning |
|---|---|---|
| `-n`, `--num-servers` | 10 | Successful destinations to keep |
| `--max-attempts` | n + 20 | Servers to try before reporting a shortfall |
| `--duration` | 60 | Seconds of data per test |
| `--interval` | 0.2 | Seconds between goodput and TCP_INFO samples |
| `--timeout` | 10 | Connect and reply timeout |
| `--representative` | median mean goodput | Destination used for Q2/Q3 plots (`IP` or `IP:PORT`) |
| `--output-dir` | `results/run_<time>` or `results/q3_<time>` | Where CSV, JSON, and PDF files are written |
| `--compare` | off | Q3: same destinations for every algorithm and repetition |
| `--algorithms` | `cubic reno bbr` | Algorithms compared when `--compare` is set |
| `--repetitions` | 3 | Repetitions of each destination and algorithm |
| `--replot RUN_DIR` | | Rebuild Q3 plots from saved measurements |

Outside Docker the same commands are `python3 main.py ...`. Dependencies are Python 3, numpy, and matplotlib (`requirements.txt`). The image installs those two from pip. The Ubuntu `python3-matplotlib` package depends on `unicode-data`, and unpacking that package fails on some Docker hosts.





## Dependencies and reproducible setup for assignment 3 option 1

Linux Kernel Version: 6.8.0-147-generic

From the repository root on Ubuntu, install the dependencies:

```sh
sudo apt update
sudo apt install -y python3 python3-venv python3-pip build-essential linux-headers-$(uname -r)
python3 -m venv .venv
source .venv/bin/activate
python -m pip install numpy matplotlib
python -c 'import numpy, matplotlib; print("NumPy:", numpy.__version__, "Matplotlib:", matplotlib.__version__)'
```

Activate `.venv` again in each new terminal before running Python commands.
On WSL2 or a custom kernel, Ubuntu's package repositories may not provide
matching headers; obtain the headers/build tree for that exact kernel before
building the module. The Makefile accepts `KDIR=/path/to/kernel/build`.

Record the environment used for a run:

```sh
uname -r
python --version
python -m pip freeze > dependencies-used.txt
```

To recreate the recorded Python package versions in a fresh virtual environment:

```sh
python -m pip install -r dependencies-used.txt
```

## Reproduce the experiments

Check which algorithms are registered and available to unprivileged processes:

```sh
cat /proc/sys/net/ipv4/tcp_available_congestion_control
cat /proc/sys/net/ipv4/tcp_allowed_congestion_control
```

If BBR is missing, load it on the Linux host with `sudo modprobe tcp_bbr`.
Build and load `hybrid` using the commands below, then check the lists again.
Every algorithm passed to `--algorithms` must be selectable by the test process.
If necessary, a host administrator can add the requested registered algorithms
to `net.ipv4.tcp_allowed_congestion_control`, preserving the existing entries.

Use the included `listed_iperf3_servers.json` input, or a CSV/JSON server list
containing addresses and ports.

Build and load on a Linux host or VM with matching kernel headers and a C
compiler (macOS cannot load Linux modules):

```sh
make -C custom_congestion_module
sudo insmod custom_congestion_module/tcp_hybrid.ko
python main.py listed_iperf3_servers.json --compare -n 5 --algorithms cubic reno bbr hybrid --duration 30 --interval 0.2 --timeout 10 --repetitions 3 --output-dir results/a3
```

For a shorter functionality check, use a separate output directory:

```sh
python main.py listed_iperf3_servers.json --compare -n 1 --algorithms cubic reno bbr hybrid --duration 10 --interval 0.2 --repetitions 1 --max-attempts 5 --output-dir results/smoke
```
