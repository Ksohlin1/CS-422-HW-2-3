import argparse
import socket_program as sp
from fetch_servers import fetch_servers

if __name__ == "__main__":
    """
    Example usage:
        python3 main.py servers.json
        python3 main.py servers.csv -n 10 --duration 60 --interval 0.2
    """

    #argument set up (copied from assignment 1)
    parser = argparse.ArgumentParser(
        description="Run iperf3-compatible TCP tests against random public iperf3 servers (HW 2, Q1 and Q2)."
    )
    parser.add_argument("path", help="JSON or CSV server list with IP/HOST and PORT columns")
    parser.add_argument("-n", "--num-servers", type=int, default=10,
                        help="number of servers to test successfully (default: %(default)s)")
    parser.add_argument("--max-attempts", type=int,
                        help="maximum number of servers to try (default: num-servers + 20)")
    parser.add_argument("--duration", type=int, default=60,
                        help="seconds to send data to each server (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=0.2,
                        help="seconds between goodput/TCP_INFO samples (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=10,
                        help="seconds to wait for a connection or a server reply (default: %(default)s)")
    args = parser.parse_args()

    if args.max_attempts is None:
        args.max_attempts = args.num_servers + 20
    if args.num_servers < 1 or args.max_attempts < 1:
        parser.error("--num-servers and --max-attempts must be at least 1")
    if args.duration < 1:
        parser.error("--duration must be at least 1 second")
    if not 0 < args.interval < args.duration:
        parser.error("--interval must be positive and shorter than --duration")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")


    #Uses assignment 1 script to get (ip, [ports]) for every server in the input file
    try:
        server_list = fetch_servers(args.path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
    if not server_list:
        raise SystemExit(f"error: no addresses resolved from {args.path}")

    sp.runHW2Q1And2(
        serverList=server_list,
        n=args.num_servers,
        max_attempts=args.max_attempts,
        test_time=args.duration,
        interval=args.interval,
        timeout_time=args.timeout,
    )
