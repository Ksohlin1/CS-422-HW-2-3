import socket
import random
import string
import json
import time
import threading
import struct
import csv
import os

import matplotlib.pyplot as plt
import numpy as np



#Constant to indicate failure, just converted a random number to bytes
FAIL = (3562835652).to_bytes(10, byteorder='big')



def runHW2Q1And2(serverList, n, max_attempts, test_time, interval, timeout_time, representative=None, out_dir=None):
    #each run writes to its own directory so repeated runs (e.g. per algorithm in Q3) don't overwrite each other
    out_dir = out_dir or os.path.join("results", time.strftime("run_%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    print(f"Saving outputs to {out_dir}")

    #part 1:
    results = connectN(serverList, n, max_attempts, test_time, interval, timeout_time, out_dir)
    Q1c_parse_and_plot(results, out_dir)
    Q2_parse_plot_modified(results, interval, representative, out_dir)






'''
Keeps track of tried servers, number of attempts
serverList is a list of (ip, [ports]) from fetch_servers; each attempt uses one random port of that server
Tries connecting and testing unique servers until either n tests succeed or we reach the max attempts
Failed attempts and their reasons are logged to failures.csv
returns a list where each entry is (IP_Adress, port, (Tuple of Results from Connect)) = (IP, port, (JSON Test Output, List of tuples: (Time from start, goodput, snd_cwnd, rtt, loss signal), algorithm))
'''
def connectN(serverList, n, max_attempts, test_time, interval, timeout_time, out_dir="."):
    valid_outputs = 0
    attempt = 0

    results = list()
    length = len(serverList)

    #index of attempted ips
    attempted = list()

    #every failed attempt is logged with its reason
    failure_log = os.path.join(out_dir, "failures.csv")
    with open(failure_log, "w", newline="") as f:
        csv.writer(f).writerow(["timestamp", "ip", "port", "reason"])


    while(valid_outputs < n):
        #if attempted all IPs return None
        if (len(attempted) == length or attempt >= max_attempts):
            if(attempt >= max_attempts):
                print("Max attempts were used")
            print(f"Warning: Shortfall in destinations. Requested {n}, but only successfully tested {valid_outputs}.")
            return results

        #Try find unattempted ip
        rand_index = random.randint(0, length - 1)
        if (rand_index in attempted):
            continue
        attempted.append(rand_index)

        #Servers often listen on a range of ports (e.g. 5201-5209); pick one at random
        ip, ports = serverList[rand_index]
        port = random.choice(ports)

        print(f"attempting test on {ip}:{port}")
        output = connect(ip, port, test_time, interval, timeout_time)
        attempt += 1
        if output[0] == FAIL:
            print(f"{ip}:{port} Failed ({output[1]}). Trying another")
            with open(failure_log, "a", newline="") as f:
                csv.writer(f).writerow([time.strftime("%Y-%m-%d %H:%M:%S"), ip, port, output[1]])
            continue

        #if didnt fail:
        print(f"{ip}:{port} Succeeded.")
        results.append((ip, port, output))
        valid_outputs +=1
    return results





'''
* Takes in one specific IP and port to connect to (the port the server list advertises)
* creates one socket, this first socket is the control channel which we use to configure our test and "control" what happens
    * Once the socket is created and connected, we configure the test infortmation in a JSON string and send it to the server
    * We wait for an acknowledgement that the server is ready for the test
* We then create a second socket which connects to the same server and sends the same session cookie, this will be out "test" socket
    *After we recieve a "ready for test" acknowledgement through the control socket, we start spamming full length messages out of the test socket for test_time seconds
* We then tell the server the test is finished and recieve the results

*If at any point the connection drops or deviates from the expected we return (FAIL, reason)
*upon success returns a tuple with the first index being the JSON output, the second index being the throughput from the thread function,
* and the third the congestion control algorithm the data socket used
*   i.e (Json, list from throughput function, algorithm)
'''
def connect(ipAdress, port, test_time, interval, timeout_time):
    #test_time: length of test in seconds
    #interval: seconds between throughput samples
    #timeout_time: max time wait before timing out
    session_cookie = "" #session cookie for the connect/test

    #socket.socket creates a socket: first parameter is specifying use ipv4, second indicates use TCP
    #with section will deal with automatic closure of socket
    #This socket is the "control channel" which is essentially used to set up and monitor the test
    #Another socket will be opened which will send the test data back and forth and that socket will be the "tested" connection
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_time)
        try:
            sock.connect((ipAdress, port))
        except OSError as e:
            return (FAIL, f"Failed to connect to server: {e}")
        
        session_cookie = generate_session_cookie()

        #try to send session_cookie identifier, close out if failed
        try:
            sock.sendall(session_cookie)
        except OSError as e:
            return (FAIL, f"Failed to transmit session cookie: {e}")




        #wait for sever to respond with state changes to PARAM_EXCHANGE (allowing us to give it paramters)
        try:
            wait_for_state(sock, 9) #9 is PARAM_EXCHANGE
        except OSError as e:
            return (FAIL, f"Failed to recieve parameter acknowledgement: {e}")



        #no send the paramters:
        #Create JSON describing the test we will do:
        data = {
            "tcp": True,                #says we are using tcp
            "omit": 0,                  #ignore 0 seconds at the start
            "time": test_time,          #test lasts test_time seconds
            "parallel": 1,              #We will open one more connection in order to do the test  
            "len": 131072,              #standard 128 kibiByte
            "client_version": "3.1.3"   #Apprently we send a random client version to reduce chance of being turned down immediatly?
        }

        # convert to string and encode in standard
        # seperators removes whitespace, 
        json_str = json.dumps(data, separators=(',', ':'))
        message_bytes = json_str.encode('utf-8')
        str_len = len(message_bytes)

        #convert the length to an a 4 byte header via big endian:
        length_header = str_len.to_bytes(4, byteorder='big')


        #Send the json data starting with the length header:
        try:
            sock.sendall(length_header)
            sock.sendall(message_bytes)
        except OSError as e:
            return (FAIL, f"Failed to transmit JSON payload: {e}")





        #wait for sever to respond with state changes acknowledging CREATE_STREAMS or PARAM_EXCHANGE 
        try:
            wait_for_state(sock, 10) #10 is CREATE_STREAMS
        except OSError as e:
            return (FAIL, f"Failed to recieve parameter acknowledgement: {e}")





        #time to open second TCP socket using same session cookie which will be used to test
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
            test_socket.settimeout(timeout_time)
            
            
            #try to connect and send session_cookie identifier, close out if failed
            try:
                test_socket.connect((ipAdress, port))
                test_socket.sendall(session_cookie)
            except OSError as e:
                return (FAIL, f"Failed to open test socket: {e}")

            #Record the algorithm the kernel actually uses on the data socket (TCP_CONGESTION is 13 on Linux)
            try:
                cc_algo = test_socket.getsockopt(socket.IPPROTO_TCP, 13, 16).split(b'\0')[0].decode()
            except OSError:
                cc_algo = "unknown" #platforms without TCP_CONGESTION (e.g. macOS)





            #Wait until we recieve the go ahead (TEST_STATE):
            #note we use the control socket here
            try:
                wait_for_state(sock, 1) #1 is TEST_START
            except OSError as e:
                return (FAIL, f"Test Socket failed to recieve go ahead: {e}")




            #data to send will be a full buffer of random shit (length matching what we put in the JSON parameter), in this case 0's
            to_send = b'0' * 131072
            end_time = time.time() + test_time
            bytes_sent = 0


            #To satisfy part C we need to spin up a thread and have it sample every `interval` seconds
            throughput_records = []
            
            poll_thread = threading.Thread(
                target=poll_throughput, 
                args=(test_socket, test_time, interval, throughput_records)
            )
            poll_thread.start()




            #send continuous data for test_time seconds:
            try:
                while time.time() < end_time:
                    test_socket.sendall(to_send)
                    bytes_sent += len(to_send)
            except OSError as e:
                return (FAIL, f"Test Socket data transmission failed: {e}")

            #join thread once test finishes
            poll_thread.join()

            #exiting test_socket with block here
        



        #tell server we are done by sending TEST_END which is state #4:
        #We then read until we get the state change acknlowedgement to EXCHANGE_RESULTS (#13):
        #finally return results once gathered
        try:
            sock.sendall((4).to_bytes(1, byteorder='big'))
            wait_for_state(sock, 13) #13 is EXCHANGE_RESULTS

            #We send our results first (bytes sent, retransmits from TCP_INFO), then read out the server's
            client_stats = json.dumps({
                "cpu_util_total": 0,
                "cpu_util_user": 0,
                "cpu_util_system": 0,
                "sender_has_retransmits": 1,
                "streams": [{
                        "id": 1,
                        "bytes": bytes_sent,
                        "retransmits": throughput_records[-1][4] if throughput_records else 0,
                        "jitter": 0,
                        "errors": 0,
                        "packets": 0,
                        "start_time": 0,
                        "end_time": test_time
                    }]
            }, separators=(',', ':')).encode('utf-8')
            sock.sendall(len(client_stats).to_bytes(4, byteorder='big'))
            sock.sendall(client_stats)

            #Read data out: 4 bytes indicating length, then the JSON
            server_res_len = int.from_bytes(recv_exact(sock, 4), byteorder='big')
            server_res_bytes = recv_exact(sock, server_res_len)
        except OSError as e:
            return (FAIL, f"Error during wrapup: {e}")

        #Finish like iperf3: server sends DISPLAY_RESULTS (14), client answers IPERF_DONE (16).
        #We already have the results, so a server hanging up here doesn't fail the test.
        try:
            wait_for_state(sock, 14)
            sock.sendall((16).to_bytes(1, byteorder='big'))
        except OSError as e:
            print(f"Warning: server did not finish cleanly: {e}")
        return (server_res_bytes, throughput_records, cc_algo)


def recv_exact(sock, length):
    #recv can return fewer bytes than asked for, so keep reading until we have them all
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError("server closed the connection")
        data += chunk
    return data


def wait_for_state(sock, expected):
    #Read state bytes until `expected` arrives; raise if the server rejects or aborts the test
    while True:
        state = struct.unpack('b', recv_exact(sock, 1))[0] #signed byte, so ACCESS_DENIED is -1
        if state == expected:
            return
        if state == -1: #ACCESS_DENIED
            raise OSError("server is busy (ACCESS_DENIED)")
        if state == -2: #SERVER_ERROR, followed by the iperf3 error code and errno
            i_errno, err = struct.unpack('!ii', recv_exact(sock, 8))
            raise OSError(f"server rejected the test (SERVER_ERROR {i_errno}, errno {err})")
        if state == 11: #SERVER_TERMINATE
            raise OSError("server terminated the test")

            

'''
Queries every `interval` seconds
returns a list of tuples where each entry is (elapsed time, goodput, snd_cwnd, rtt, loss signal,
    rttvar, pacing_rate, bytes_acked, delivery_rate, bytes_sent, bytes_retrans)
'''
def poll_throughput(sock, duration, interval, records_list):
    #off set might need to be changed, 11 works in WSL
    TCP_INFO = 11 
    last_bytes_acked = 0
    last_time = time.time()
    start_time = last_time
    
    while time.time() - start_time < duration:
        time.sleep(interval)
        
        try:
            # Request 256 bytes (larger than any current TCP_INFO struct).
            # getsockopt will only return the actual bytes the kernel uses.
            tcp_info_raw = sock.getsockopt(socket.SOL_TCP, TCP_INFO, 256)
            
            # 128 bytes is the minimum struct length required to safely read
            # the 8-byte bytes_acked value starting at offset 120.
            if len(tcp_info_raw) < 128:
                print(f"Error: Kernel too old. TCP_INFO size is {len(tcp_info_raw)} bytes.")
                break
                
            # Extract metrics using standard Linux tcp_info byte offsets
            # I = unsigned int, Q = unsigned long long 
            rtt_us = struct.unpack_from('I', tcp_info_raw, 68)[0]          # tcpi_rtt (microseconds)
            snd_cwnd = struct.unpack_from('I', tcp_info_raw, 80)[0]        # tcpi_snd_cwnd (in MSS / segments)
            total_retrans = struct.unpack_from('I', tcp_info_raw, 100)[0]  # tcpi_total_retrans (cumulative lost/retransmitted packets)
            bytes_acked = struct.unpack_from('Q', tcp_info_raw, 120)[0]    # tcpi_bytes_acked (bytes)

            # Recommended extras; later fields are None if this kernel's struct is too short for them
            rttvar_us = struct.unpack_from('I', tcp_info_raw, 72)[0]       # tcpi_rttvar (microseconds)
            pacing_rate = struct.unpack_from('Q', tcp_info_raw, 104)[0]    # tcpi_pacing_rate (bytes/s)
            delivery_rate = struct.unpack_from('Q', tcp_info_raw, 160)[0] if len(tcp_info_raw) >= 168 else None  # tcpi_delivery_rate (bytes/s)
            bytes_sent, bytes_retrans = struct.unpack_from('QQ', tcp_info_raw, 200) if len(tcp_info_raw) >= 216 else (None, None)  # tcpi_bytes_sent, tcpi_bytes_retrans (bytes)
            
        except OSError as e:
            print(f"Failed to get socket stats: {e}")
            break
            
        current_time = time.time()
        delta_t = current_time - last_time
        delta_bytes = bytes_acked - last_bytes_acked
        
        goodput_bps = (delta_bytes / delta_t) * 8
        
        #
        records_list.append((
            current_time - start_time, 
            goodput_bps, 
            snd_cwnd, 
            rtt_us, 
            total_retrans,
            rttvar_us,
            pacing_rate,
            bytes_acked,
            delivery_rate,
            bytes_sent,
            bytes_retrans
        ))
        
        last_bytes_acked = bytes_acked
        last_time = current_time




        


def Q1c_parse_and_plot(results, out_dir="."):
    if not results:
        print("No successful results to parse and plot.")
        return

    num_servers = len(results)
    
    # Create subplots: one panel per destination server
    fig, axes = plt.subplots(num_servers, 1, figsize=(10, 4 * num_servers), sharex=True)
    
    # If there's only one server, matplotlib returns a single axis object instead of an array
    if num_servers == 1:
        axes = [axes]
        
    # Print the summary table header
    print(f"\n{'Destination':<22} | {'Algorithm':<10} | {'Min (Mbps)':<12} | {'Median (Mbps)':<13} | {'Mean (Mbps)':<12} | {'95th Pct (Mbps)':<15}")
    print("-" * 96)
    summary_rows = []  # also saved to q1_summary.csv for the report
    
    for i, (ip, port, output) in enumerate(results):
        json_bytes, records, algo = output
        destination = f"{ip}:{port}"
        
        # Handle edge case where a test completed but no throughput records were polled
        if not records:
            print(f"{destination:<22} | {algo:<10} | {'N/A':<12} | {'N/A':<13} | {'N/A':<12} | {'N/A':<15}")
            continue
            
        # Extract times and convert goodput from bps to Mbps
        times = [rec[0] for rec in records]
        goodputs_mbps = [rec[1] / 1e6 for rec in records]
        
        # Calculate statistics using numpy
        min_val = np.min(goodputs_mbps)
        median_val = np.median(goodputs_mbps)
        mean_val = np.mean(goodputs_mbps)
        pct_95 = np.percentile(goodputs_mbps, 95)
        
        # Print row for this destination
        print(f"{destination:<22} | {algo:<10} | {min_val:<12.2f} | {median_val:<13.2f} | {mean_val:<12.2f} | {pct_95:<15.2f}")
        summary_rows.append([ip, port, algo, f"{min_val:.2f}", f"{median_val:.2f}", f"{mean_val:.2f}", f"{pct_95:.2f}"])
        
        # Plot the data on its specific subplot panel
        axes[i].plot(times, goodputs_mbps, marker='o', linestyle='-', markersize=3, label="Goodput")
        axes[i].set_title(f"Throughput over Time: {destination}")
        axes[i].set_ylabel("Goodput (Mbps)")
        axes[i].grid(True, linestyle='--', alpha=0.7)
    
    summary_csv = os.path.join(out_dir, "q1_summary.csv")
    with open(summary_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["destination_ip", "port", "algorithm", "min_mbps", "median_mbps", "mean_mbps", "p95_mbps"])
        writer.writerows(summary_rows)
    print(f"\nSummary table saved to {summary_csv}")

    # Format the overall plot
    axes[-1].set_xlabel("Time (seconds)")
    plt.tight_layout()
    
    # Export the plot
    plot_pdf = os.path.join(out_dir, "throughput_over_time.pdf")
    plt.savefig(plot_pdf)
    print(f"\nPlot saved to {plot_pdf}")











def Q2_parse_plot(results):
    if not results:
        print("No successful results to parse and plot for Q2.")
        return

    # 1. Detect the active TCP congestion control algorithm from Linux kernel (fallback to 'cubic')
    try:
        with open("/proc/sys/net/ipv4/tcp_congestion_control", "r") as f:
            cc_algo = f.read().strip()
    except OSError:
        cc_algo = "(Assumed) cubic"

    run_id = f"run_{int(time.time())}"
    all_runs_json = []
    csv_filename = "q2_tcp_stats.csv"
    json_filename = "q2_tcp_stats.json"

    # 2. Part 2(a): Process data and store measurements in both CSV and JSON
    with open(csv_filename, mode="w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        # Write CSV header including metadata and explicit units
        writer.writerow([
            "run_id", "destination_ip", "algorithm",
            "timestamp_s", "goodput_mbps", "snd_cwnd_segments",
            "rtt_ms", "interval_retrans_pkts", "cumulative_retrans_pkts"
        ])

        for ip, output in results:
            _, records = output
            if not records:
                continue

            prev_retrans = 0
            formatted_samples = []

            for rec in records:
                timestamp_s = rec[0]
                goodput_mbps = rec[1] / 1e6       # Convert bps -> Mbps
                snd_cwnd = rec[2]                 # In MSS segments
                rtt_ms = rec[3] / 1000.0          # Convert microseconds -> milliseconds
                total_retrans = rec[4]            # Cumulative retransmissions

                # Compute per-interval retransmissions (loss proxy during this 200ms window)
                interval_retrans = max(0, total_retrans - prev_retrans)
                prev_retrans = total_retrans

                # Write row to CSV
                writer.writerow([
                    run_id, ip, cc_algo,
                    f"{timestamp_s:.3f}", f"{goodput_mbps:.4f}", snd_cwnd,
                    f"{rtt_ms:.3f}", interval_retrans, total_retrans
                ])

                # Append to JSON sample list
                formatted_samples.append({
                    "timestamp_s": round(timestamp_s, 3),
                    "goodput_mbps": round(goodput_mbps, 4),
                    "snd_cwnd_segments": snd_cwnd,
                    "rtt_ms": round(rtt_ms, 3),
                    "interval_retrans_pkts": interval_retrans,
                    "cumulative_retrans_pkts": total_retrans
                })

            all_runs_json.append({
                "run_id": run_id,
                "destination": ip,
                "algorithm": cc_algo,
                "units": {
                    "timestamp": "seconds (s)",
                    "goodput": "Megabits per second (Mbps)",
                    "snd_cwnd": "Maximum Segment Size (MSS) segments",
                    "rtt": "milliseconds (ms)",
                    "interval_retrans": "packets retransmitted per 200ms interval",
                    "cumulative_retrans": "total packets retransmitted"
                },
                "samples": formatted_samples
            })

    # Save JSON file
    with open(json_filename, mode="w") as jf:
        json.dump(all_runs_json, jf, indent=2)

    print(f"Q2 measurements saved to {csv_filename} and {json_filename} (Algorithm: {cc_algo}, Run ID: {run_id})")

    # 3. Part 2(b): Pick a representative destination (first IP with valid records)
    rep_run = next((r for r in all_runs_json if r["samples"]), None)
    if not rep_run:
        print("No valid sample records found to plot for Q2.")
        return

    rep_ip = rep_run["destination"]
    samples = rep_run["samples"]

    times = [s["timestamp_s"] for s in samples]
    goodputs = [s["goodput_mbps"] for s in samples]
    cwnds = [s["snd_cwnd_segments"] for s in samples]
    rtts = [s["rtt_ms"] for s in samples]
    interval_losses = [s["interval_retrans_pkts"] for s in samples]
    cum_losses = [s["cumulative_retrans_pkts"] for s in samples]

    # --- Plot (i): Time Series (snd_cwnd, RTT, loss proxy, and throughput) ---
    fig_ts, axes_ts = plt.subplots(4, 1, figsize=(10, 12), sharex=True)

    # 1. Throughput (Goodput)
    axes_ts[0].plot(times, goodputs, color="tab:blue", marker="o", markersize=2, linestyle="-")
    axes_ts[0].set_title(f"TCP Time Series ({cc_algo.upper()}) - Destination: {rep_ip}")
    axes_ts[0].set_ylabel("Goodput (Mbps)")
    axes_ts[0].grid(True, linestyle="--", alpha=0.7)

    # 2. Congestion Window (snd_cwnd)
    axes_ts[1].plot(times, cwnds, color="tab:orange", marker="o", markersize=2, linestyle="-")
    axes_ts[1].set_ylabel("snd_cwnd (Segments)")
    axes_ts[1].grid(True, linestyle="--", alpha=0.7)

    # 3. Round Trip Time (RTT)
    axes_ts[2].plot(times, rtts, color="tab:green", marker="o", markersize=2, linestyle="-")
    axes_ts[2].set_ylabel("RTT (ms)")
    axes_ts[2].grid(True, linestyle="--", alpha=0.7)

    # 4. Loss Proxy (Interval & Cumulative Retransmissions)
    axes_ts[3].step(times, interval_losses, where="post", color="tab:red", label="Interval Retrans (200ms)")
    axes_ts[3].set_ylabel("Interval Retrans (pkts)", color="tab:red")
    axes_ts[3].tick_params(axis="y", labelcolor="tab:red")
    axes_ts[3].set_xlabel("Time (seconds)")
    axes_ts[3].grid(True, linestyle="--", alpha=0.7)

    # Add secondary axis on the loss subplot for cumulative retransmissions
    ax_cum = axes_ts[3].twinx()
    ax_cum.plot(times, cum_losses, color="tab:purple", linestyle="--", alpha=0.7, label="Cumulative Retrans")
    ax_cum.set_ylabel("Total Retrans (pkts)", color="tab:purple")
    ax_cum.tick_params(axis="y", labelcolor="tab:purple")

    plt.tight_layout()
    ts_pdf = f"q2_timeseries_{rep_ip}.pdf"
    plt.savefig(ts_pdf)
    plt.close(fig_ts)


    # --- Plot (ii): Scatter Plots (Relationships with Goodput) ---
    fig_sc, axes_sc = plt.subplots(1, 3, figsize=(15, 4.5))
    fig_sc.suptitle(f"TCP Metrics vs. Goodput ({cc_algo.upper()}) - Destination: {rep_ip}")

    # 1. snd_cwnd vs Goodput
    axes_sc[0].scatter(cwnds, goodputs, color="tab:orange", alpha=0.6, edgecolors="k", linewidth=0.5)
    axes_sc[0].set_title("snd_cwnd vs. Goodput")
    axes_sc[0].set_xlabel("snd_cwnd (MSS Segments)")
    axes_sc[0].set_ylabel("Goodput (Mbps)")
    axes_sc[0].grid(True, linestyle="--", alpha=0.7)

    # 2. RTT vs Goodput
    axes_sc[1].scatter(rtts, goodputs, color="tab:green", alpha=0.6, edgecolors="k", linewidth=0.5)
    axes_sc[1].set_title("RTT vs. Goodput")
    axes_sc[1].set_xlabel("RTT (ms)")
    axes_sc[1].set_ylabel("Goodput (Mbps)")
    axes_sc[1].grid(True, linestyle="--", alpha=0.7)

    # 3. Loss Signal vs Goodput
    axes_sc[2].scatter(interval_losses, goodputs, color="tab:red", alpha=0.6, edgecolors="k", linewidth=0.5)
    axes_sc[2].set_title("Loss Signal vs. Goodput")
    axes_sc[2].set_xlabel("Retransmissions per 200ms Interval")
    axes_sc[2].set_ylabel("Goodput (Mbps)")
    axes_sc[2].grid(True, linestyle="--", alpha=0.7)

    plt.tight_layout()
    sc_pdf = f"q2_scatter_{rep_ip}.pdf"
    plt.savefig(sc_pdf)
    plt.close(fig_sc)



from matplotlib.backends.backend_pdf import PdfPages
def Q2_parse_plot_modified(results, interval, representative=None, out_dir="."):
    if not results:
        print("No successful results to parse and plot for Q2.")
        return

    interval_label = f"{interval * 1000:g}ms"  # e.g. "200ms", used in units and plot labels

    # 1. Algorithm(s) actually used, as read from each data socket in connect()
    cc_algo = ", ".join(sorted({output[2] for _, _, output in results}))

    run_id = os.path.basename(os.path.abspath(out_dir))  # the run's output directory name
    all_runs_json = []
    csv_filename = os.path.join(out_dir, "q2_tcp_stats.csv")
    json_filename = os.path.join(out_dir, "q2_tcp_stats.json")

    # 2. Part 2(a): Process data and store measurements in both CSV and JSON
    with open(csv_filename, mode="w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        # Write CSV header including metadata and explicit units
        writer.writerow([
            "run_id", "destination_ip", "port", "algorithm",
            "timestamp_s", "goodput_mbps", "snd_cwnd_segments",
            "rtt_ms", "interval_retrans_pkts", "cumulative_retrans_pkts",
            "rttvar_ms", "pacing_rate_mbps", "delivery_rate_mbps",
            "bytes_acked", "bytes_sent", "bytes_retrans"
        ])

        for ip, port, output in results:
            _, records, algo = output
            if not records:
                continue

            prev_retrans = 0
            formatted_samples = []

            for rec in records:
                timestamp_s = rec[0]
                goodput_mbps = rec[1] / 1e6      # Convert bps -> Mbps
                snd_cwnd = rec[2]                # In MSS segments
                rtt_ms = rec[3] / 1000.0         # Convert microseconds -> milliseconds
                total_retrans = rec[4]           # Cumulative retransmissions
                rttvar_ms = rec[5] / 1000.0      # Convert microseconds -> milliseconds
                pacing_mbps = round(rec[6] * 8 / 1e6, 4)  # Convert bytes/s -> Mbps
                delivery_mbps = round(rec[8] * 8 / 1e6, 4) if rec[8] is not None else None
                bytes_acked, bytes_sent, bytes_retrans = rec[7], rec[9], rec[10]  # Cumulative byte counters

                # Compute per-interval retransmissions (loss proxy during this sampling interval)
                interval_retrans = max(0, total_retrans - prev_retrans)
                prev_retrans = total_retrans

                # Write row to CSV (fields this kernel doesn't report are left empty)
                writer.writerow([
                    run_id, ip, port, algo,
                    f"{timestamp_s:.3f}", f"{goodput_mbps:.4f}", snd_cwnd,
                    f"{rtt_ms:.3f}", interval_retrans, total_retrans,
                    f"{rttvar_ms:.3f}", pacing_mbps, delivery_mbps,
                    bytes_acked, bytes_sent, bytes_retrans
                ])

                # Append to JSON sample list
                formatted_samples.append({
                    "timestamp_s": round(timestamp_s, 3),
                    "goodput_mbps": round(goodput_mbps, 4),
                    "snd_cwnd_segments": snd_cwnd,
                    "rtt_ms": round(rtt_ms, 3),
                    "interval_retrans_pkts": interval_retrans,
                    "cumulative_retrans_pkts": total_retrans,
                    "rttvar_ms": round(rttvar_ms, 3),
                    "pacing_rate_mbps": pacing_mbps,
                    "delivery_rate_mbps": delivery_mbps,
                    "bytes_acked": bytes_acked,
                    "bytes_sent": bytes_sent,
                    "bytes_retrans": bytes_retrans
                })

            all_runs_json.append({
                "run_id": run_id,
                "destination": ip,
                "port": port,
                "algorithm": algo,
                "units": {
                    "timestamp": "seconds (s)",
                    "goodput": "Megabits per second (Mbps)",
                    "snd_cwnd": "Maximum Segment Size (MSS) segments",
                    "rtt": "milliseconds (ms)",
                    "interval_retrans": f"packets retransmitted per {interval_label} interval",
                    "cumulative_retrans": "total packets retransmitted",
                    "rttvar": "milliseconds (ms)",
                    "pacing_rate": "Megabits per second (Mbps)",
                    "delivery_rate": "Megabits per second (Mbps)",
                    "byte_counters": "cumulative bytes (bytes_acked, bytes_sent, bytes_retrans)"
                },
                "samples": formatted_samples
            })

    # Save JSON file
    with open(json_filename, mode="w") as jf:
        json.dump(all_runs_json, jf, indent=2)

    print(f"Q2 measurements saved to {csv_filename} and {json_filename} (Algorithm: {cc_algo}, Run ID: {run_id})")

    # 3. Part 2(b): Plot a representative destination: the one given (IP or IP:PORT), otherwise
    # the one with the median mean goodput, i.e. a typical path rather than an outlier
    runs = [run for run in all_runs_json if run["samples"]]
    if not runs:
        print("No valid sample records found to plot for Q2.")
        return
    rep_runs = [run for run in runs if representative in (run["destination"], f"{run['destination']}:{run['port']}")]
    if representative and not rep_runs:
        print(f"Warning: representative {representative} was not tested successfully, choosing one instead")
    if not rep_runs:
        runs.sort(key=lambda run: np.mean([s["goodput_mbps"] for s in run["samples"]]))
        rep_runs = [runs[len(runs) // 2]]
    print(f"Representative destination: {rep_runs[0]['destination']}:{rep_runs[0]['port']}")

    pdf_filename = os.path.join(out_dir, "Q2.pdf")

    with PdfPages(pdf_filename) as pdf:

        for run in rep_runs:
            samples = run["samples"]

            # Skip destinations without valid samples
            if not samples:
                continue

            rep_ip = f"{run['destination']}:{run['port']}"  # shown in the plot titles

            # Extract data for this destination
            times = [s["timestamp_s"] for s in samples]
            goodputs = [s["goodput_mbps"] for s in samples]
            cwnds = [s["snd_cwnd_segments"] for s in samples]
            rtts = [s["rtt_ms"] for s in samples]
            interval_losses = [
                s["interval_retrans_pkts"] for s in samples
            ]
            cum_losses = [
                s["cumulative_retrans_pkts"] for s in samples
            ]

            # --------------------------------------------------
            # Page 1: Time Series
            # --------------------------------------------------
            fig_ts, axes_ts = plt.subplots(
                4, 1, figsize=(10, 12), sharex=True
            )

            # 1. Throughput (Goodput)
            axes_ts[0].plot(
                times, goodputs,
                color="tab:blue", marker="o",
                markersize=2, linestyle="-"
            )
            axes_ts[0].set_title(
                f"TCP Time Series ({cc_algo.upper()}) - "
                f"Destination: {rep_ip}"
            )
            axes_ts[0].set_ylabel("Goodput (Mbps)")
            axes_ts[0].grid(True, linestyle="--", alpha=0.7)

            # 2. Congestion Window
            axes_ts[1].plot(
                times, cwnds,
                color="tab:orange", marker="o",
                markersize=2, linestyle="-"
            )
            axes_ts[1].set_ylabel("snd_cwnd (Segments)")
            axes_ts[1].grid(True, linestyle="--", alpha=0.7)

            # 3. Round Trip Time
            axes_ts[2].plot(
                times, rtts,
                color="tab:green", marker="o",
                markersize=2, linestyle="-"
            )
            axes_ts[2].set_ylabel("RTT (ms)")
            axes_ts[2].grid(True, linestyle="--", alpha=0.7)

            # 4. Interval and cumulative retransmissions
            axes_ts[3].step(
                times, interval_losses,
                where="post", color="tab:red",
                label=f"Interval Retrans ({interval_label})"
            )
            axes_ts[3].set_ylabel(
                "Interval Retrans (pkts)", color="tab:red"
            )
            axes_ts[3].tick_params(
                axis="y", labelcolor="tab:red"
            )
            axes_ts[3].set_xlabel("Time (seconds)")
            axes_ts[3].grid(True, linestyle="--", alpha=0.7)

            # Secondary axis for cumulative retransmissions
            ax_cum = axes_ts[3].twinx()
            ax_cum.plot(
                times, cum_losses,
                color="tab:purple", linestyle="--",
                alpha=0.7, label="Cumulative Retrans"
            )
            ax_cum.set_ylabel(
                "Total Retrans (pkts)", color="tab:purple"
            )
            ax_cum.tick_params(
                axis="y", labelcolor="tab:purple"
            )

            fig_ts.tight_layout()

            # Append this destination's time-series plot
            pdf.savefig(fig_ts)
            plt.close(fig_ts)

            # --------------------------------------------------
            # Page 2: Scatter Plots
            # --------------------------------------------------
            fig_sc, axes_sc = plt.subplots(
                1, 3, figsize=(15, 4.5)
            )
            fig_sc.suptitle(
                f"TCP Metrics vs. Goodput ({cc_algo.upper()}) - "
                f"Destination: {rep_ip}"
            )

            # 1. Congestion window vs. Goodput
            axes_sc[0].scatter(
                cwnds, goodputs,
                color="tab:orange", alpha=0.6,
                edgecolors="k", linewidth=0.5
            )
            axes_sc[0].set_title("snd_cwnd vs. Goodput")
            axes_sc[0].set_xlabel("snd_cwnd (MSS Segments)")
            axes_sc[0].set_ylabel("Goodput (Mbps)")
            axes_sc[0].grid(True, linestyle="--", alpha=0.7)

            # 2. RTT vs. Goodput
            axes_sc[1].scatter(
                rtts, goodputs,
                color="tab:green", alpha=0.6,
                edgecolors="k", linewidth=0.5
            )
            axes_sc[1].set_title("RTT vs. Goodput")
            axes_sc[1].set_xlabel("RTT (ms)")
            axes_sc[1].set_ylabel("Goodput (Mbps)")
            axes_sc[1].grid(True, linestyle="--", alpha=0.7)

            # 3. Interval retransmissions vs. Goodput
            axes_sc[2].scatter(
                interval_losses, goodputs,
                color="tab:red", alpha=0.6,
                edgecolors="k", linewidth=0.5
            )
            axes_sc[2].set_title("Loss Signal vs. Goodput")
            axes_sc[2].set_xlabel(
                f"Retransmissions per {interval_label} Interval"
            )
            axes_sc[2].set_ylabel("Goodput (Mbps)")
            axes_sc[2].grid(True, linestyle="--", alpha=0.7)

            fig_sc.tight_layout(rect=[0, 0, 1, 0.92])

            # Append this destination's scatter plots
            pdf.savefig(fig_sc)
            plt.close(fig_sc)

    print(
        f"Successfully saved Q2 plots into multi-page PDF: "
        f"{pdf_filename}"
    )





def generate_session_cookie():
    #make an array of characters and numbers
    characters = string.ascii_letters + string.digits
    
    # randomly select 36 from the array
    rnd_str = ''.join(random.choices(characters, k=36))

    return rnd_str.encode('ascii') + b'\0'
