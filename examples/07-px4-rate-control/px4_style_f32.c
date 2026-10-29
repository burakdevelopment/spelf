#include <stdbool.h>

#define RADIANS(d) ((d) * 0.017453292519943295f)

typedef struct {
    float gain_p[3], gain_i[3], gain_d[3], gain_ff[3], lim_int[3];
    float rate_int[3];
    bool sat_pos[3], sat_neg[3];
} px4_rate_control_t;

static int finite_(float x) { return x == x && x - x == 0.0f; }

static float min_(float a, float b) { return a < b ? a : b; }
static float max_(float a, float b) { return a > b ? a : b; }
static float constrain_(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void update_integral(px4_rate_control_t *rc, float rate_error[3], float dt) {
    for (int i = 0; i < 3; i++) {
        if (rc->sat_pos[i]) { rate_error[i] = min_(rate_error[i], 0.0f); }
        if (rc->sat_neg[i]) { rate_error[i] = max_(rate_error[i], 0.0f); }
        float i_factor = rate_error[i] / RADIANS(400.0f);
        i_factor = max_(0.0f, 1.0f - i_factor * i_factor);
        float rate_i = rc->rate_int[i] + i_factor * rc->gain_i[i] * rate_error[i] * dt;
        if (finite_(rate_i)) {
            rc->rate_int[i] = constrain_(rate_i, -rc->lim_int[i], rc->lim_int[i]);
        }
    }
}

void px4_rate_control_update(px4_rate_control_t *rc, const float rate[3], const float rate_sp[3],
                             const float accel[3], float dt, bool landed, float torque[3]) {
    float err[3];
    for (int i = 0; i < 3; i++) {
        err[i] = rate_sp[i] - rate[i];
        torque[i] = rc->gain_p[i] * err[i] + rc->rate_int[i] - rc->gain_d[i] * accel[i]
                    + rc->gain_ff[i] * rate_sp[i];
    }
    if (!landed) { update_integral(rc, err, dt); }
}
