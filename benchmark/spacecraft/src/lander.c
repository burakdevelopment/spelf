#include <stdint.h>
#include <stdbool.h>
typedef struct { int32_t status; bool value; } leg_t;
typedef struct { int32_t status; float value; } alt_t;
typedef struct { int32_t count; int32_t need; bool set; } debounce_t;
static const float ARM_ALT = 40.0f, HOVER = 0.38f, KV = 0.08f, KH = 0.1f;
static debounce_t armed = { 0, 5, false };
static debounce_t touchdown = { 0, 3, false };

static void debounce(debounce_t *d, bool c)
{
    if (c) {
        if (d->count < d->need) {
            d->count += 1;
        }
        if (d->count >= d->need) {
            d->set = true;
        }
    } else {
        d->count = 0;
    }
}

static int32_t leg(leg_t r)
{
    return ((r.status == 0) && r.value) ? 1 : 0;
}

float f_lander_step(alt_t alt, float vz, leg_t l1, leg_t l2, leg_t l3)
{
    float vref;
    if (alt.status == 0) {
        debounce(&armed, alt.value < ARM_ALT);
        const float v = 0.0f - KH * alt.value - 1.0f;
        vref = (v < -20.0f) ? -20.0f : v;
    } else {
        debounce(&armed, false);
        vref = -2.0f;
    }
    const int32_t n = leg(l1) + leg(l2) + leg(l3);
    debounce(&touchdown, armed.set && (n >= 2));
    float t;
    if (touchdown.set) {
        t = 0.0f;
    } else {
        const float u = HOVER + KV * (vref - vz);
        t = (u < 0.1f) ? 0.1f : ((u > 1.0f) ? 1.0f : u);
    }
    return t;
}
