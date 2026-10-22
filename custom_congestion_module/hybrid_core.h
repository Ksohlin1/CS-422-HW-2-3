/* SPDX-License-Identifier: GPL-2.0 */
#ifndef HYBRID_CORE_H
#define HYBRID_CORE_H

/* Integer arithmetic: RTT/time in microseconds, rates in bytes/second,
 * loss in parts per million, and fractional cwnd in millionths. */
#ifdef __KERNEL__
#include <linux/math64.h>
#define HY_DIV(a, b) div64_u64((a), (b))
#else
#include <stdint.h>
#include <stdbool.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define HY_DIV(a, b) ((a) / (b))
#endif

#define HY_INTERVAL 200000U
#define HY_MIN_RTT_WINDOW 10000000U
#define HY_MIN_CWND 2U
#define HY_SCALE 1000000ULL

struct hybrid {
    u64 previous_goodput, best_goodput;
    u32 min_rtt, min_stamp, cooldown_until;
    u32 saved_cwnd, fraction, loss_ewma;
    u32 high_delay_count, plateau_count;
    bool startup, have_previous, cooldown;
};

static inline bool hy_elapsed(u32 now, u32 start, u32 duration)
{
    return (u32)(now - start) >= duration;
}

static inline u32 hy_reduce(u32 cwnd, u32 percent)
{
    u32 value = HY_DIV((u64)cwnd * percent, 100);
    return value < HY_MIN_CWND ? HY_MIN_CWND : value;
}

static inline void hy_init(struct hybrid *h, u32 now)
{
    *h = (struct hybrid){ .min_stamp = now, .startup = true };
}

/* The kernel owns recovery. Cancel a pending probe restore so that it cannot
 * undo an RTO/fast-recovery reduction, and suppress interval reductions. */
static inline void hy_recovery(struct hybrid *h, u32 now, u32 rtt)
{
    h->saved_cwnd = 0;
    h->startup = false;
    h->high_delay_count = 0;
    h->fraction = 0;
    h->cooldown = true;
    h->cooldown_until = now + (rtt ? rtt : HY_INTERVAL);
}

static inline u32 hy_step(struct hybrid *h, u32 cwnd, u32 now,
                         u32 rtt, u32 loss, u32 sent, u64 goodput,
                         bool limited, bool recovering)
{
    u32 queue, rate;
    bool stable, congestive;

    if (!rtt)
        return cwnd;
    if (recovering) {
        hy_recovery(h, now, rtt);
        return cwnd;
    }
    if (h->saved_cwnd) {
        cwnd = h->saved_cwnd;
        h->saved_cwnd = 0;
        /* Accept a fresh baseline after draining, even if the path got slower. */
        h->min_rtt = rtt;
        h->min_stamp = now;
        h->high_delay_count = 0;
    }
    if (!h->min_rtt || rtt <= h->min_rtt) {
        h->min_rtt = rtt;
        h->min_stamp = now;
    }
    queue = HY_DIV((u64)(rtt - h->min_rtt) * HY_SCALE, h->min_rtt);
    stable = !h->have_previous || goodput * 100 >= h->previous_goodput * 95;
    rate = sent ? HY_DIV((u64)loss * HY_SCALE, sent) : (loss ? HY_SCALE : 0);
    if (rate > HY_SCALE)
        rate = HY_SCALE;
    /* EWMA alpha = 1/8. */
    h->loss_ewma = (7 * h->loss_ewma + rate) / 8;
    congestive = (loss && queue > 50000) || h->loss_ewma > 20000;
    h->high_delay_count = queue > 150000 ? h->high_delay_count + 1 : 0;
    if (h->startup) {
        if (goodput * 100 >= h->best_goodput * 125) {
            h->best_goodput = goodput;
            h->plateau_count = 0;
        } else if (++h->plateau_count >= 3) {
            h->startup = false;
        }
    }
    if (h->cooldown && (int)(now - h->cooldown_until) >= 0)
        h->cooldown = false;
    if (h->cooldown) {
        /* Hold through the recovery cooldown. */
    } else if (congestive) {
        cwnd = hy_reduce(cwnd, 70);
        hy_recovery(h, now, rtt);
    } else if (h->high_delay_count >= 2) {
        cwnd = hy_reduce(cwnd, 90);
        h->high_delay_count = 0;
        h->startup = false;
        h->fraction = 0;
    } else if (limited && queue < 50000 && stable) {
        if (h->startup) {
            cwnd += 200;
        } else {
            u64 increase = h->fraction + HY_DIV(HY_SCALE * HY_INTERVAL, rtt);
            cwnd += HY_DIV(increase, HY_SCALE);
            h->fraction = increase % HY_SCALE;
        }
    }
    h->previous_goodput = goodput;
    h->have_previous = true;
    if (!h->cooldown && hy_elapsed(now, h->min_stamp, HY_MIN_RTT_WINDOW + 1)) {
        h->saved_cwnd = cwnd;
        cwnd = hy_reduce(cwnd, 50);
    }
    return cwnd;
}
#endif
