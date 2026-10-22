'''
Q3(a): compare congestion control algorithms (e.g. CUBIC, Reno, BBR) on the same destinations.

Run through main.py:  python3 main.py servers.json --compare --algorithms cubic reno bbr --repetitions 3

* Picks n destinations once, with short probe tests, so every algorithm is measured on the same ip:port
* Test order is interleaved: for each repetition, for each destination, every algorithm runs back-to-back,
  and the algorithm list is rotated by one each repetition (rep 1: cubic reno bbr, rep 2: reno bbr cubic, ...)
* Each run is logged in run_log.csv; each (algorithm, repetition) gets the Q1/Q2 outputs in <algo>_rep<k>/
* comparison/ holds q3_summary.csv and the PDFs comparing algorithms on one representative destination
* --replot DIR rebuilds the comparison from a finished run's saved measurements (e.g. for another representative)
* Runs where the client was frozen (e.g. the host slept) are cut at the freeze for the comparison only
'''
import csv
import json
import os
import platform
import socket
import time

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

import socket_program as sp


PROBE_TIME = 3   #seconds per probe test when picking destinations
RETRY_DELAY = 5  #seconds before retrying a failed run once; public servers are often briefly busy
PAUSE = 2        #seconds between tests; iperf3 resets connections that arrive while it resets after a test
TCP_CONGESTION = 13  #Linux socket option number


def runHW2Q3(serverList, n, max_attempts, test_time, interval, timeout_time, algorithms, repetitions,
             representative=None, out_dir=None):
    out_dir = out_dir or os.path.join("results", time.strftime("q3_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    print(f"Saving outputs to {out_dir}")
    sp.record_host_environment(out_dir)
    check_algorithms(algorithms)

    #1. pick the destinations once (system default algorithm, short tests); failures go to selection/failures.csv
    selection_dir = os.path.join(out_dir, "selection")
    os.makedirs(selection_dir, exist_ok=True)
    print(f"\nPicking {n} destinations with {PROBE_TIME} s probe tests")
    probes = sp.connectN(serverList, n, max_attempts, PROBE_TIME, interval, timeout_time, selection_dir)
    destinations = [(ip, port) for ip, port, _ in probes]
    if not destinations:
        print("No destination accepted a probe test; nothing to compare.")
        return

    total = len(destinations) * len(algorithms) * repetitions
    print(f"\nRunning {len(destinations)} destinations x {len(algorithms)} algorithms x {repetitions} repetitions"
          f" = {total} tests, about {total * (test_time + 2) / 60:.0f} minutes")

    config = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kernel": platform.release(),
        "available_algorithms": _read_proc("tcp_available_congestion_control"),
        "allowed_algorithms": _read_proc("tcp_allowed_congestion_control"),
        "default_algorithm": _read_proc("tcp_congestion_control"),
        "algorithms": algorithms,
        "repetitions": repetitions,
        "duration_s": test_time,
        "interval_s": interval,
        "timeout_s": timeout_time,
        "test_order": "interleaved: for each repetition, for each destination, every algorithm back-to-back; "
                      "the algorithm list is rotated by one each repetition",
        "destinations": [f"{ip}:{port}" for ip, port in destinations],
        "representative": None,
    }
    config_path = os.path.join(out_dir, "config.json")
    _write_json(config_path, config)

    #2. interleaved runs; results[(algo, rep)] has the same shape as connectN's return value
    results = {(algo, rep): [] for algo in algorithms for rep in range(1, repetitions + 1)}
    log_path = os.path.join(out_dir, "run_log.csv")
    with open(log_path, "w", newline="") as f:
        csv.writer(f).writerow(["seq", "repetition", "start_time", "ip", "port", "requested_algorithm",
                                "used_algorithm", "status", "reason"])

    seq = 0
    for rep in range(1, repetitions + 1):
        shift = (rep - 1) % len(algorithms)
        order = algorithms[shift:] + algorithms[:shift]
        for ip, port in destinations:
            for algo in order:
                seq += 1
                start = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{seq}/{total}] repetition {rep}: {ip}:{port} with {algo}")
                time.sleep(PAUSE)
                output = sp.connect(ip, port, test_time, interval, timeout_time, algo)
                if output[0] == sp.FAIL:
                    print(f"  failed ({output[1]}), retrying once in {RETRY_DELAY} s")
                    time.sleep(RETRY_DELAY)
                    output = sp.connect(ip, port, test_time, interval, timeout_time, algo)

                ok = output[0] != sp.FAIL
                if ok:
                    results[(algo, rep)].append((ip, port, output))
                    print(f"  done (algorithm used: {output[2]})")
                else:
                    print(f"  failed again ({output[1]}), skipping")
                with open(log_path, "a", newline="") as f:
                    csv.writer(f).writerow([seq, rep, start, ip, port, algo, output[2] if ok else "",
                                            "ok" if ok else "failed", "" if ok else output[1]])

    #3. one representative destination for every algorithm and repetition
    rep_dest = pick_representative(results, destinations, algorithms, representative)
    if rep_dest is None:
        print("No successful runs; nothing to plot.")
        return
    config["representative"] = rep_dest
    _write_json(config_path, config)
    print(f"\nRepresentative destination for all algorithms: {rep_dest}")

    #4. Q1/Q2 outputs for each (algorithm, repetition)
    for (algo, rep), runs in results.items():
        if runs:
            run_dir = os.path.join(out_dir, f"{algo}_rep{rep}")
            os.makedirs(run_dir, exist_ok=True)
            sp.Q1c_parse_and_plot(runs, run_dir)
            sp.Q2_parse_plot_modified(runs, interval, rep_dest, run_dir)

    #5. comparison across algorithms
    cmp_dir = os.path.join(out_dir, "comparison")
    os.makedirs(cmp_dir, exist_ok=True)
    results = drop_frozen_samples(results, interval, cmp_dir)
    Q3_summary(results, algorithms, cmp_dir)
    Q3_plot(results, algorithms, repetitions, rep_dest, interval, cmp_dir)
    print(f"\nQ3 outputs saved to {out_dir} (run log: {log_path})")


def replotHW2Q3(run_dir, representative=None):
    '''
    Rebuild the comparison from a finished run's saved measurements (<algo>_rep<k>/q2_tcp_stats.json),
    e.g. to plot a different representative destination without re-running the experiment.
    Writes to <run_dir>/comparison_<ip>_<port>/ so the original comparison/ is kept.
    '''
    with open(os.path.join(run_dir, "config.json")) as f:
        config = json.load(f)
    algorithms, repetitions = config["algorithms"], config["repetitions"]
    destinations = [(name.rsplit(":", 1)[0], int(name.rsplit(":", 1)[1])) for name in config["destinations"]]

    #results[(algo, rep)] in the same shape runHW2Q3 builds; samples become poll_throughput-style records
    results = {(algo, rep): [] for algo in algorithms for rep in range(1, repetitions + 1)}
    for algo, rep in results:
        path = os.path.join(run_dir, f"{algo}_rep{rep}", "q2_tcp_stats.json")
        if not os.path.exists(path):
            continue  #every run of this (algorithm, repetition) failed
        with open(path) as f:
            for run in json.load(f):
                records = [(s["timestamp_s"], s["goodput_mbps"] * 1e6, s["snd_cwnd_segments"], s["rtt_ms"] * 1000,
                            s["cumulative_retrans_pkts"]) for s in run["samples"]]
                results[(algo, rep)].append((run["destination"], run["port"], (b"", records, run["algorithm"])))

    rep_dest = pick_representative(results, destinations, algorithms, representative)
    if rep_dest is None:
        print(f"No saved measurements in {run_dir}; nothing to plot.")
        return
    print(f"Representative destination: {rep_dest}")
    cmp_dir = os.path.join(run_dir, "comparison_" + rep_dest.replace(":", "_"))
    os.makedirs(cmp_dir, exist_ok=True)
    results = drop_frozen_samples(results, config["interval_s"], cmp_dir)
    Q3_summary(results, algorithms, cmp_dir)
    Q3_plot(results, algorithms, repetitions, rep_dest, config["interval_s"], cmp_dir)


def drop_frozen_samples(results, interval, out_dir):
    '''
    A gap between samples far longer than the interval means the client was frozen (e.g. the host slept),
    so the sample after it averages over the freeze. Each run is cut at its first such gap for the comparison;
    the raw per-run CSV/JSON keep every sample. Cuts are listed in excluded_samples.csv.
    '''
    limit = max(5 * interval, 1.0)
    cleaned, excluded = {}, []
    for (algo, rep), runs in results.items():
        cleaned[(algo, rep)] = []
        for ip, port, output in runs:
            records = output[1]
            cut = next((i for i in range(1, len(records)) if records[i][0] - records[i - 1][0] > limit), len(records))
            if cut < len(records):
                excluded.append([algo, rep, f"{ip}:{port}", f"{records[cut - 1][0]:.3f}", f"{records[cut][0]:.3f}",
                                 len(records) - cut])
                print(f"Warning: {algo} rep {rep} {ip}:{port} paused from {records[cut - 1][0]:.1f} s to "
                      f"{records[cut][0]:.1f} s; excluding the {len(records) - cut} sample(s) from then on")
            cleaned[(algo, rep)].append((ip, port, (output[0], records[:cut], output[2])))

    with open(os.path.join(out_dir, "excluded_samples.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["algorithm", "repetition", "destination", "last_good_sample_s", "resumed_at_s", "samples_excluded"])
        writer.writerows(excluded)
    return cleaned


def check_algorithms(algorithms):
    #Fail before any testing if the kernel won't let us select an algorithm
    for algo in algorithms:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.setsockopt(socket.IPPROTO_TCP, TCP_CONGESTION, algo.encode())
            except OSError as e:
                raise SystemExit(
                    f"error: cannot select '{algo}' on a socket ({e}).\n"
                    f"  available: {_read_proc('tcp_available_congestion_control')}\n"
                    f"  allowed:   {_read_proc('tcp_allowed_congestion_control')}\n"
                    f"  On the Ubuntu host: sudo ./host_setup.sh\n"
                    f"  Start the container with --network host so it uses that host kernel and network namespace.\n"
                    f"  A private network namespace needs --cap-add NET_ADMIN, and the host must already have loaded tcp_{algo}.")


def pick_representative(results, destinations, algorithms, representative):
    '''
    The destination given with --representative (IP or IP:PORT), otherwise the destination with the
    median mean goodput under the first algorithm, among destinations that succeeded in every run
    '''
    names = [f"{ip}:{port}" for ip, port in destinations]
    if representative:
        for ip, port in destinations:
            if representative in (ip, f"{ip}:{port}"):
                return f"{ip}:{port}"
        print(f"Warning: representative {representative} is not one of the destinations, choosing one instead")

    succeeded = {name: 0 for name in names}
    for runs in results.values():
        for ip, port, output in runs:
            if output[1]:
                succeeded[f"{ip}:{port}"] += 1
    complete = [name for name in names if succeeded[name] == len(results)]
    candidates = complete or [name for name in names if succeeded[name]]
    if not candidates:
        return None

    def mean_goodput(name):
        values = [rec[1] for (algo, _), runs in results.items() if algo == algorithms[0]
                  for ip, port, output in runs if f"{ip}:{port}" == name for rec in output[1]]
        return np.mean(values) if values else 0
    candidates.sort(key=mean_goodput)
    return candidates[len(candidates) // 2]


def _stats(record_lists):
    #Aggregate statistics over all samples of several runs (each run is a list of poll_throughput records)
    goodput = [rec[1] / 1e6 for records in record_lists for rec in records]
    rtt = [rec[3] / 1000.0 for records in record_lists for rec in records]
    run_means = [np.mean([rec[1] / 1e6 for rec in records]) for records in record_lists]
    return [
        len(record_lists), len(goodput),
        f"{np.min(goodput):.2f}", f"{np.median(goodput):.2f}", f"{np.mean(goodput):.2f}",
        f"{np.percentile(goodput, 95):.2f}", f"{np.std(run_means):.2f}",
        f"{np.median(rtt):.3f}", f"{np.mean(rtt):.3f}", f"{np.percentile(rtt, 95):.3f}",
        sum(records[-1][4] for records in record_lists),
    ]


def Q3_summary(results, algorithms, out_dir):
    '''
    q3_summary.csv: per algorithm over all destinations and repetitions ("all"), then per destination.
    goodput_run_mean_std is the spread of per-run mean goodput, i.e. the variability across runs.
    '''
    header = ["algorithm", "destination", "runs", "samples",
              "goodput_min_mbps", "goodput_median_mbps", "goodput_mean_mbps", "goodput_p95_mbps",
              "goodput_run_mean_std_mbps", "rtt_median_ms", "rtt_mean_ms", "rtt_p95_ms", "total_retrans_pkts"]
    rows = []
    for algo in algorithms:
        runs = [(f"{ip}:{port}", output[1]) for (a, _), algo_runs in results.items() if a == algo
                for ip, port, output in algo_runs if output[1]]
        if not runs:
            continue
        rows.append([algo, "all"] + _stats([records for _, records in runs]))
        for dest in sorted({name for name, _ in runs}):
            rows.append([algo, dest] + _stats([records for name, records in runs if name == dest]))

    path = os.path.join(out_dir, "q3_summary.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)

    print(f"\n{'Algorithm':<10} | {'Runs':<5} | {'Median (Mbps)':<13} | {'Mean (Mbps)':<12} | {'Run std (Mbps)':<14} | "
          f"{'Median RTT (ms)':<15} | {'Retrans':<8}")
    print("-" * 97)
    for row in rows:
        if row[1] == "all":
            print(f"{row[0]:<10} | {row[2]:<5} | {row[5]:<13} | {row[6]:<12} | {row[8]:<14} | {row[9]:<15} | {row[12]:<8}")
    print(f"Q3 summary saved to {path}")


def Q3_plot(results, algorithms, repetitions, rep_dest, interval, out_dir):
    '''
    For the representative destination, one page per repetition with one column per algorithm:
      Q3_timeseries.pdf: goodput, snd_cwnd, RTT and retransmissions per interval over time
      Q3_scatter.pdf:    snd_cwnd, RTT and retransmissions per interval against goodput
    Every panel showing the same metric uses the same axis limits so algorithms compare directly.
    Q3_variability.pdf: per-run mean goodput and median RTT for each algorithm, over all destinations and runs.
    '''
    interval_label = f"{interval * 1000:g}ms"

    #series[(algo, rep)] = dict of lists for the representative destination, or None if that run failed
    series = {}
    for algo in algorithms:
        for rep in range(1, repetitions + 1):
            records = next((output[1] for ip, port, output in results[(algo, rep)]
                            if f"{ip}:{port}" == rep_dest and output[1]), None)
            if records is None:
                series[(algo, rep)] = None
                continue
            cumulative = [rec[4] for rec in records]
            series[(algo, rep)] = {
                "time": [rec[0] for rec in records],
                "goodput": [rec[1] / 1e6 for rec in records],
                "cwnd": [rec[2] for rec in records],
                "rtt": [rec[3] / 1000.0 for rec in records],
                "loss": [max(0, c - p) for p, c in zip([0] + cumulative[:-1], cumulative)],
            }

    present = [s for s in series.values() if s]
    if not present:
        print(f"No successful runs on {rep_dest}; skipping Q3 plots.")
        return
    limits = {key: max(max(s[key]) for s in present) * 1.05 or 1 for key in ("time", "goodput", "cwnd", "rtt", "loss")}
    labels = {
        "goodput": "Goodput (Mbps)",
        "cwnd": "snd_cwnd (segments)",
        "rtt": "RTT (ms)",
        "loss": f"Retrans per {interval_label}",
    }
    cols = len(algorithms)

    ts_path = os.path.join(out_dir, "Q3_timeseries.pdf")
    with PdfPages(ts_path) as pdf:
        for rep in range(1, repetitions + 1):
            fig, axes = plt.subplots(4, cols, figsize=(5 * cols, 11), squeeze=False)
            fig.suptitle(f"Q3 time series - {rep_dest} - repetition {rep}")
            for j, algo in enumerate(algorithms):
                s = series[(algo, rep)]
                axes[0][j].set_title(algo.upper())
                for i, key in enumerate(("goodput", "cwnd", "rtt", "loss")):
                    ax = axes[i][j]
                    if s:
                        if key == "loss":
                            ax.step(s["time"], s[key], where="post", color="tab:red")
                        else:
                            ax.plot(s["time"], s[key], marker="o", markersize=2)
                    else:
                        ax.text(0.5, 0.5, "run failed", ha="center", va="center", transform=ax.transAxes)
                    ax.set_xlim(0, limits["time"])
                    ax.set_ylim(0, limits[key])
                    ax.grid(True, linestyle="--", alpha=0.7)
                    if j == 0:
                        ax.set_ylabel(labels[key])
                axes[-1][j].set_xlabel("Time (seconds)")
            fig.tight_layout(rect=[0, 0, 1, 0.96])
            pdf.savefig(fig)
            plt.close(fig)

    sc_path = os.path.join(out_dir, "Q3_scatter.pdf")
    with PdfPages(sc_path) as pdf:
        for rep in range(1, repetitions + 1):
            fig, axes = plt.subplots(3, cols, figsize=(5 * cols, 10), squeeze=False)
            fig.suptitle(f"Q3 metrics vs. goodput - {rep_dest} - repetition {rep}")
            for j, algo in enumerate(algorithms):
                s = series[(algo, rep)]
                axes[0][j].set_title(algo.upper())
                for i, key in enumerate(("cwnd", "rtt", "loss")):
                    ax = axes[i][j]
                    if s:
                        ax.scatter(s[key], s["goodput"], alpha=0.6, edgecolors="k", linewidth=0.5)
                    else:
                        ax.text(0.5, 0.5, "run failed", ha="center", va="center", transform=ax.transAxes)
                    ax.set_xlim(0, limits[key])
                    ax.set_ylim(0, limits["goodput"])
                    ax.set_xlabel(labels[key])
                    ax.grid(True, linestyle="--", alpha=0.7)
                    if j == 0:
                        ax.set_ylabel(labels["goodput"])
            fig.tight_layout(rect=[0, 0, 1, 0.96])
            pdf.savefig(fig)
            plt.close(fig)

    #variability across runs, over every destination
    run_means, run_rtts, names = [], [], []
    for algo in algorithms:
        record_lists = [output[1] for (a, _), runs in results.items() if a == algo for _, _, output in runs if output[1]]
        if record_lists:
            names.append(algo.upper())
            run_means.append([np.mean([rec[1] / 1e6 for rec in records]) for records in record_lists])
            run_rtts.append([np.median([rec[3] / 1000.0 for rec in records]) for records in record_lists])
    var_path = os.path.join(out_dir, "Q3_variability.pdf")
    fig, (ax_g, ax_r) = plt.subplots(1, 2, figsize=(11, 4.5))
    ax_g.boxplot(run_means)
    ax_g.set_xticks(range(1, len(names) + 1), names)
    ax_g.set_ylabel("Mean goodput per run (Mbps)")
    ax_g.set_title("Goodput across runs (all destinations)")
    ax_r.boxplot(run_rtts)
    ax_r.set_xticks(range(1, len(names) + 1), names)
    ax_r.set_ylabel("Median RTT per run (ms)")
    ax_r.set_title("RTT across runs (all destinations)")
    for ax in (ax_g, ax_r):
        ax.grid(True, linestyle="--", alpha=0.7)
    fig.tight_layout()
    fig.savefig(var_path)
    plt.close(fig)

    print(f"Q3 plots saved to {ts_path}, {sc_path} and {var_path}")


def _read_proc(name):
    try:
        with open(f"/proc/sys/net/ipv4/{name}") as f:
            return f.read().strip()
    except OSError:
        return None


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
