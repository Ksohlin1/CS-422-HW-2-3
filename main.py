import argparse
import socket_program as sp
from fetch_servers import fetch_ips

if __name__ == "__main__":
    """
    Example usage:
        python3 main.py servers.json
        python3 main.py servers.csv
    """

    #argument set up (copied from assignment 1)
    parser = argparse.ArgumentParser(
        description="Ping a list of servers and plot geographic distance against RTT."
    )
    parser.add_argument("path", help="JSON or CSV file listing the servers to measure")
    parser.add_argument("N", type = int, help="Number of servers to test in HW 2, Q1")
    args = parser.parse_args()


    #Uses assignment 1 script to get list of ips from input file
    try:
        ip_list = fetch_ips(args.path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")

    sp.runHW2Q1And2(iplist=ip_list, n=args.N, max_attempts = args.N + 20)


    



    