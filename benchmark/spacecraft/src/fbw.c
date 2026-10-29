#include <stdint.h>
#include <stdbool.h>
static const float QMAX = 0.35f, ALIM = 0.45f, KLIM = 2.0f, DT = 0.0125f, RSTEP = 0.0125f;
static float xi = 0.0f;
static float dprev = 0.0f;

static float med3(float a, float b, float c)
{
    const float lo = (a < b) ? a : b;
    const float hi = (a < b) ? b : a;
    const float m = (hi < c) ? hi : c;
    return (lo < m) ? m : lo;
}

static float sched(float qb, float k0, float k1, float k2, float k3)
{
    float r;
    if (qb < 10000.0f) {
        const float w = (qb - 1000.0f) / 9000.0f;
        r = k0 + (k1 - k0) * w;
    } else if (qb < 30000.0f) {
        const float w = (qb - 10000.0f) / 20000.0f;
        r = k1 + (k2 - k1) * w;
    } else {
        const float w = (qb - 30000.0f) / 30000.0f;
        r = k2 + (k3 - k2) * w;
    }
    return r;
}

static float rate_lim(float d, float p)
{
    const float up = p + RSTEP;
    const float dn = p - RSTEP;
    return (d > up) ? up : ((d < dn) ? dn : d);
}

float f_fbw_step(float stick, float q1, float q2, float q3, float a1, float a2, float a3, float qb)
{
    const float q = med3(q1, q2, q3);
    const float a = med3(a1, a2, a3);
    const float kq = sched(qb, 1.2f, 0.6f, 0.35f, 0.22f) * 1.0f;
    const float ka = sched(qb, 3.0f, 1.6f, 0.9f, 0.55f);
    const float ki = sched(qb, 1.5f, 0.8f, 0.45f, 0.3f);
    const float excess = (a > ALIM) ? (a - ALIM) : 0.0f;
    const float qcmd = stick * QMAX - KLIM * excess;
    const float e = qcmd - q;
    const float raw = kq * e + ki * xi - ka * a;
    float d;
    if ((raw >= -0.44f) && (raw <= 0.44f)) {
        d = raw;
        const float xn = xi + e * DT;
        xi = (xn > 1.0f) ? 1.0f : ((xn < -1.0f) ? -1.0f : xn);
    } else {
        d = (raw < -0.44f) ? -0.44f : 0.44f;
    }
    const float lim = rate_lim(d, dprev);
    dprev = lim;
    return lim;
}
