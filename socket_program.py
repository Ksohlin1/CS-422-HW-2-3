import socket
import random
import string
import json
import time
import threading
import struct
import csv

import matplotlib.pyplot as plt
import numpy as np



#Constant to indicate failure, just converted a random number to bytes
FAIL = (3562835652).to_bytes(10, byteorder='big')



def runHW2Q1And2(iplist, n, max_attempts):
    #part 1:
    results = connectN(iplist, n, max_attempts)
    Q1c_parse_and_plot(results)
    Q2_parse_plot_modified(results)






'''
Keeps track of tried ips, number of attempts
Tries connecting and testing unique ips until either n tests succeed or we reach the max attempts
returns a list where each entry is (IP_Adress, (Touple of Results from Connect)) = (IP, (JSON Test Output, List of touples: (Time from start, goodput)))
'''
def connectN(ipList, n, max_attempts):
    valid_outputs = 0
    attempt = 0

    results = list()
    length = len(ipList)

    #index of attempted ips
    attempted = list()


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

        print("attempting test on " + ipList[rand_index])
        output = connect(ipList[rand_index])
        attempt += 1
        if output[0] == FAIL:
            print("IP Adress " + ipList[rand_index] + " Failed. Trying another")
            continue

        #if didnt fail:
        print("IP Adress " + ipList[rand_index] + " Succeeded.")
        results.append((ipList[rand_index], output))
        valid_outputs +=1
    return results





'''
* Takes in one specific IP to connect to
* Assumes a standard port that iperf3 uses
* creates one socket, this first socket is the control channel which we use to configure our test and "control" what happens
    * Once the socket is created and connected, we configure the test infortmation in a JSON string and send it to the server
    * We wait for an acknowledgement that the server is ready for the test
* We then create a second socket which connects to the same server and sends the same session cookie, this will be out "test" socket
    *After we recieve a "ready for test" acknowledgement through the control socket, we start spamming full length messages out of the test socket for test_time seconds
* We then tell the server the test is finished and recieve the results

*If at any point the connection drops or deviates from the expected we return -1
*upon success returns a tuple with the first index being the JSON output, and the second index being the throughput from the thread function
*   i.e (Json, list from throughput function)
'''
def connect(ipAdress):
    port = 5201 #assumed standard port for iperf3
    test_time = 45 #length of test in seconds
    timeout_time = 10 #max time wait before timing out
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
            print(f"Failed to connect to server: {e}")
            return (FAIL, FAIL)
        
        session_cookie = generate_session_cookie()

        #try to send session_cookie identifier, close out if failed
        try:
            sock.sendall(session_cookie)
        except OSError as e:
            print(f"Failed to transmit session cookie: {e}")
            return (FAIL, FAIL)




        #wait for sever to respond with state changes to PARAM_EXCHANGE (allowing us to give it paramters)
        try:
            acknowledged = False
            while (not acknowledged):
                data1 = sock.recv(1) #use 1 here in case server sending multiple state changes
    
                #9 is PARAM_EXCHANGE saying parameters were recieved      
                #must convert 9 from integers to raw bytes                         
                if (data1 == (9).to_bytes(1, byteorder='big')):                             
                    acknowledged = True
    
                #If the connection closes, recv will instantly return an empty string so we need to check:
                if (not data1):
                    raise OSError
        except OSError as e:
            print(f"Failed to recieve parameter acknowledgement: {e}")
            return (FAIL, FAIL)



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
            print(f"Failed to transmit JSON payload: {e}")
            return (FAIL, FAIL)





        #wait for sever to respond with state changes acknowledging CREATE_STREAMS or PARAM_EXCHANGE 
        try:
            acknowledged = False
            while (not acknowledged):
               data1 = sock.recv(1) #use 1 here in case server sending multiple state changes

               #10 is CREATE_STREAMS acknowledgement state (saying a stream has been created)                  
               if (data1 == (10).to_bytes(1, byteorder='big')):                             
                   acknowledged = True

                #If the connection closes, recv will instantly return an empty string so we need to check:
               if (not data1):
                   raise OSError

        except OSError as e:
            print(f"Failed to recieve parameter acknowledgement: {e}")
            return (FAIL, FAIL)





        #time to open second TCP socket using same session cookie which will be used to test
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
            test_socket.settimeout(timeout_time)
            test_socket.connect((ipAdress, port))
            
            
            #try to send session_cookie identifier, close out if failed
            try:
                test_socket.sendall(session_cookie)
            except OSError as e:
                print(f"Failed to transmit session cookie for test socket: {e}")
                return (FAIL, FAIL)





            #Wait until we recieve the go ahead (TEST_STATE):
            #note we use the control socket here
            try:
                acknowledged = False
                while (not acknowledged):
                    data2 = sock.recv(1) #use 1 here in case server sending multiple state changes        
                    if (data2 == (1).to_bytes(1, byteorder='big')):                             
                        acknowledged = True   

                    #If the connection closes:
                    if (not data2):
                        raise OSError
            except OSError as e:
                print(f"Test Socket failed to recieve go ahead: {e}")
                return (FAIL, FAIL)




            #data to send will be a full buffer of random shit (length matching what we put in the JSON parameter), in this case 0's
            to_send = b'0' * 131072
            end_time = time.time() + test_time


            #To satisfy part C we need to spin up a thread and have it sample every 200ms using the 
            throughput_records = []
            
            poll_thread = threading.Thread(
                target=poll_throughput, 
                args=(test_socket, test_time, 0.2, throughput_records)
            )
            poll_thread.start()




            #send continuous data for test_time seconds:
            try:
                while time.time() < end_time:
                    test_socket.sendall(to_send)
            except OSError as e:
                print(f"Test Socket data transmission failed: {e}")
                return (FAIL, FAIL)

            #join thread once test finishes
            poll_thread.join()

            #exiting test_socket with block here
        



        #tell server we are done by sending TEST_END which is state #4:
        #We then read until we get the state change acknlowedgement to EXCHANGE_RESULTS (#13):
        #finally return results once gathered
        try:
            sock.sendall((4).to_bytes(1, byteorder='big'))


            
            while True:
                data3 = sock.recv(1)

                #If the connection closes:
                if (not data3):
                    raise OSError

                #State moved to EXCHANGE_RESULTS (13)
                #We send a dummy JSON because the server needs it, then read out the results
                if data3 == (13).to_bytes(1, byteorder='big'): 
                    client_stats = json.dumps({
                        "cpu_util_total": 0,
                        "cpu_util_user": 0,
                        "cpu_util_system": 0,
                        "sender_has_retransmits": 0,
                        "streams": [{
                                "id": 1,
                                "bytes": 0,
                                "retransmits": -1,
                                "jitter": 0,
                                "errors": 0,
                                "packets": 0,
                                "start_time": 0,
                                "end_time": test_time
                            }]
                    }, separators=(',', ':')).encode('utf-8')
                    
                    sock.sendall(len(client_stats).to_bytes(4, byteorder='big'))
                    sock.sendall(client_stats)



                    #Read data out:
                    #First recieve the 4 bytes indicating length:
                    length_bytes = sock.recv(4)
                    if len(length_bytes) != 4:
                        raise OSError("Server connection closed before reading out data")
                    server_res_len = int.from_bytes(length_bytes, byteorder='big')
                    
                    # Read the output based on length until finished, return
                    server_res_bytes = b''
                    while len(server_res_bytes) < server_res_len:
                        chunk = sock.recv(server_res_len - len(server_res_bytes))
                        if not chunk:
                            break
                        server_res_bytes += chunk
                        
                    return (server_res_bytes, throughput_records)#

            return (FAIL, FAIL) #if makes it outside the while loop
                


                
        except OSError as e:
            print(f"Error during wrapup: {e}")
            return (FAIL, FAIL)

            

'''
Quries every 200ms 
returns a list of touples where each entry is (elapsed time, goodput, snd_cwnd, rtt, loss signal)
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
            total_retrans
        ))
        
        last_bytes_acked = bytes_acked
        last_time = current_time




        


def Q1c_parse_and_plot(results):
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
    print(f"\n{'IP Address':<18} | {'Min (Mbps)':<12} | {'Median (Mbps)':<13} | {'Mean (Mbps)':<12} | {'95th Pct (Mbps)':<15}")
    print("-" * 79)
    
    for i, (ip, output) in enumerate(results):
        json_bytes, records = output
        
        # Handle edge case where a test completed but no throughput records were polled
        if not records:
            print(f"{ip:<18} | {'N/A':<12} | {'N/A':<13} | {'N/A':<12} | {'N/A':<15}")
            continue
            
        # Extract times and convert goodput from bps to Mbps
        times = [rec[0] for rec in records]
        goodputs_mbps = [rec[1] / 1e6 for rec in records]
        
        # Calculate statistics using numpy
        min_val = np.min(goodputs_mbps)
        median_val = np.median(goodputs_mbps)
        mean_val = np.mean(goodputs_mbps)
        pct_95 = np.percentile(goodputs_mbps, 95)
        
        # Print row for this IP
        print(f"{ip:<18} | {min_val:<12.2f} | {median_val:<13.2f} | {mean_val:<12.2f} | {pct_95:<15.2f}")
        
        # Plot the data on its specific subplot panel
        axes[i].plot(times, goodputs_mbps, marker='o', linestyle='-', markersize=3, label="Goodput")
        axes[i].set_title(f"Throughput over Time: {ip}")
        axes[i].set_ylabel("Goodput (Mbps)")
        axes[i].grid(True, linestyle='--', alpha=0.7)
    
    # Format the overall plot
    axes[-1].set_xlabel("Time (seconds)")
    plt.tight_layout()
    
    # Export the plot
    plt.savefig("throughput_over_time.pdf")
    print("\nPlot saved to throughput_over_time.pdf")











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
def Q2_parse_plot_modified(results):
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
                goodput_mbps = rec[1] / 1e6      # Convert bps -> Mbps
                snd_cwnd = rec[2]                # In MSS segments
                rtt_ms = rec[3] / 1000.0         # Convert microseconds -> milliseconds
                total_retrans = rec[4]           # Cumulative retransmissions

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

    # 3. Part 2(b): Plot all destinations into a single multi-page PDF
    pdf_filename = "Q2.pdf"

    # Keep one PdfPages context open for ALL destinations.
    with PdfPages(pdf_filename) as pdf:

        for run in all_runs_json:
            samples = run["samples"]

            # Skip destinations without valid samples
            if not samples:
                continue

            rep_ip = run["destination"]

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
                label="Interval Retrans (200ms)"
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
                "Retransmissions per 200ms Interval"
            )
            axes_sc[2].set_ylabel("Goodput (Mbps)")
            axes_sc[2].grid(True, linestyle="--", alpha=0.7)

            fig_sc.tight_layout(rect=[0, 0, 1, 0.92])

            # Append this destination's scatter plots
            pdf.savefig(fig_sc)
            plt.close(fig_sc)

    print(
        f"Successfully saved all Q2 plots into multi-page PDF: "
        f"{pdf_filename}"
    )





def generate_session_cookie():
    #make an array of characters and numbers
    characters = string.ascii_letters + string.digits
    
    # randomly select 36 from the array
    rnd_str = ''.join(random.choices(characters, k=36))

    return rnd_str.encode('ascii') + b'\0'
