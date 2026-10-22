# User space for the CS 422 experiment pipeline.
# TCP congestion control and TCP_INFO come from the host kernel
# (Ubuntu 22.04+ or WSL2), not from this image. Prepare the host with
# host_setup.sh, then run the container with --network host.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# numpy and matplotlib come from pip wheels. The Ubuntu python3-matplotlib
# package depends on unicode-data, and unpacking that deb fails on some hosts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        ca-certificates \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && mkdir -p /tmp/matplotlib

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py socket_program.py compare.py fetch_servers.py listed_iperf3_servers.json ./

# Q3 pipeline: 5 destinations, 30 s each, 3 repetitions of CUBIC, Reno, and BBR.
# Sampling interval stays at the main.py default of 0.2 s.
ENTRYPOINT ["python3", "main.py"]
CMD ["listed_iperf3_servers.json", "--compare", "-n", "5", "--duration", "30", "--repetitions", "3", "--output-dir", "results/q3_final"]
