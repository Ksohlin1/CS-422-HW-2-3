#include <assert.h>
#include <stdio.h>
#include "hybrid_core.h"

int main(void)
{
    struct hybrid h;
    u32 cwnd;
    hy_init(&h, 0);
    assert(hy_step(&h, 10, 200000, 0, 0, 100, 1000, true, false) == 10);
    cwnd = hy_step(&h, 10, 200000, 100000, 0, 100, 1000, true, false);
    assert(cwnd == 210);
    assert(hy_step(&h, cwnd, 400000, 100000, 0, 100, 1300, false, false) == cwnd);
    assert(hy_step(&h, cwnd, 600000, 120000, 0, 100, 1300, true, false) == cwnd);
    cwnd = hy_step(&h, cwnd, 800000, 120000, 0, 100, 1300, true, false);
    assert(cwnd == 189 && !h.startup);
    cwnd = hy_step(&h, cwnd, 1000000, 110000, 1, 1000, 1300, true, false);
    assert(cwnd == 132);
    assert(hy_step(&h, cwnd, 1050000, 110000, 1, 1000, 1300, true, false) == cwnd);

    hy_init(&h, 0);
    h.startup = false;
    cwnd = hy_step(&h, 100, 200000, 400000, 0, 100, 1000, true, false);
    assert(cwnd == 100);
    assert(hy_step(&h, cwnd, 400000, 400000, 0, 100, 1000, true, false) == 101);
    assert(hy_step(&h, 100, 600000, 400000, 0, 100, 900, true, false) == 100);

    hy_init(&h, 0);
    h.startup = false;
    h.min_rtt = 100000;
    cwnd = hy_step(&h, 100, 10200000, 110000, 0, 100, 1000, true, false);
    assert(cwnd == 50 && h.saved_cwnd == 100);
    cwnd = hy_step(&h, cwnd, 10400000, 105000, 0, 100, 1000, false, false);
    assert(cwnd == 100 && h.saved_cwnd == 0 && h.min_rtt == 105000);
    h.saved_cwnd = 100;
    hy_recovery(&h, 10600000, 100000);
    assert(h.saved_cwnd == 0);
    assert(hy_step(&h, 1, 10800000, 100000, 4, 100, 1000, true, true) == 1);

    hy_init(&h, 0);
    assert(hy_step(&h, 100, 200000, 100000, 30, 100, 1000, true, false) == 70);
    assert(h.loss_ewma == 37500);
    assert(hy_reduce(2, 70) == 2);
    assert(hy_elapsed(100, UINT32_MAX - 100, 200));

    hy_init(&h, 0);
    cwnd = 10;
    for (u32 i = 1; i <= 4; ++i)
        cwnd = hy_step(&h, cwnd, i * HY_INTERVAL, 100000, 0, 100, 1000, true, false);
    assert(!h.startup && cwnd == 612);
    puts("hybrid core tests passed");
    return 0;
}
