// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <net/tcp.h>
#include "hybrid_core.h"

struct hybrid_ca {
    struct hybrid control;
    u64 bytes_acked;
    u32 stamp, retrans, sent;
    bool limited, recovery_pending;
};

static void hybrid_init(struct sock *sk)
{
    struct hybrid_ca *ca = inet_csk_ca(sk);
    struct tcp_sock *tp = tcp_sk(sk);

    memset(ca, 0, sizeof(*ca));
    ca->stamp = tcp_clock_us();
    hy_init(&ca->control, ca->stamp);
    ca->bytes_acked = tp->bytes_acked;
    ca->retrans = tp->total_retrans;
    ca->sent = tp->data_segs_out;
    tcp_snd_cwnd_set(tp, 10);
    tp->snd_ssthresh = TCP_INFINITE_SSTHRESH;
}

static void hybrid_state(struct sock *sk, u8 state)
{
    struct hybrid_ca *ca = inet_csk_ca(sk);

    if (state == TCP_CA_Loss || state == TCP_CA_Recovery || state == TCP_CA_CWR) {
        ca->recovery_pending = true;
        hy_recovery(&ca->control, tcp_clock_us(), tcp_sk(sk)->srtt_us >> 3);
    }
}

static u32 hybrid_ssthresh(struct sock *sk)
{
    struct hybrid_ca *ca = inet_csk_ca(sk);

    ca->recovery_pending = true;
    hy_recovery(&ca->control, tcp_clock_us(), tcp_sk(sk)->srtt_us >> 3);
    /* Fast recovery keeps 70%; Linux independently collapses cwnd on RTO. */
    return hy_reduce(tcp_snd_cwnd(tcp_sk(sk)), 70);
}

static void hybrid_acked(struct sock *sk, const struct ack_sample *sample)
{
    struct hybrid_ca *ca = inet_csk_ca(sk);

    ca->limited |= tcp_is_cwnd_limited(sk) ||
                   sample->in_flight >= tcp_snd_cwnd(tcp_sk(sk));
}

static void hybrid_control(struct sock *sk, u32 ack, u32 acked)
{
    struct hybrid_ca *ca = inet_csk_ca(sk);
    struct tcp_sock *tp = tcp_sk(sk);
    u32 now = tcp_clock_us(), elapsed = now - ca->stamp;
    u32 rtt = tp->srtt_us >> 3;
    bool recovering = inet_csk(sk)->icsk_ca_state >= TCP_CA_CWR || ca->recovery_pending;
    u64 goodput;
    u32 cwnd;

    ca->limited |= tcp_is_cwnd_limited(sk);
    if (elapsed < HY_INTERVAL)
        return;
    goodput = div64_u64((tp->bytes_acked - ca->bytes_acked) * USEC_PER_SEC, elapsed);
    cwnd = hy_step(&ca->control, tcp_snd_cwnd(tp), now, rtt,
                   tp->total_retrans - ca->retrans,
                   tp->data_segs_out - ca->sent, goodput, ca->limited, recovering);
    /* Preserve Linux's one-segment RTO window during recovery. */
    if (!recovering)
        tcp_snd_cwnd_set(tp, min(tp->snd_cwnd_clamp, max(HY_MIN_CWND, cwnd)));
    ca->stamp = now;
    ca->bytes_acked = tp->bytes_acked;
    ca->retrans = tp->total_retrans;
    ca->sent = tp->data_segs_out;
    ca->limited = false;
    ca->recovery_pending = false;
}

static struct tcp_congestion_ops hybrid_ops __read_mostly = {
    .init = hybrid_init,
    .ssthresh = hybrid_ssthresh,
    .undo_cwnd = tcp_reno_undo_cwnd,
    .set_state = hybrid_state,
    .pkts_acked = hybrid_acked,
    .cong_avoid = hybrid_control,
    .owner = THIS_MODULE,
    .name = "hybrid",
    .flags = TCP_CONG_NON_RESTRICTED,
};

static int __init hybrid_register(void)
{
    BUILD_BUG_ON(sizeof(struct hybrid_ca) > ICSK_CA_PRIV_SIZE);
    return tcp_register_congestion_control(&hybrid_ops);
}

static void __exit hybrid_unregister(void)
{
    tcp_unregister_congestion_control(&hybrid_ops);
}

module_init(hybrid_register);
module_exit(hybrid_unregister);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("HW3 hybrid delay and loss congestion control");
MODULE_ALIAS("tcp_hybrid");
