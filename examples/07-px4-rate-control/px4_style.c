#include <stdbool.h>

#define RADIANS(d) ((d) * 0.017453292519943295)

typedef struct {
    double gain_p[3], gain_i[3], gain_d[3], gain_ff[3], lim_int[3];
    double rate_int[3];
    bool sat_pos[3], sat_neg[3];
} px4_rate_control_t;

static int finite_(double x) { return x == x && x - x == 0.0; }

static double min_(double a, double b) { return a < b ? a : b; }
static double max_(double a, double b) { return a > b ? a : b; }
static double constrain_(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void update_integral(px4_rate_control_t *rc, double rate_error[3], double dt) {
    for (int i = 0; i < 3; i++) {
        if (rc->sat_pos[i]) { rate_error[i] = min_(rate_error[i], 0.0); }
        if (rc->sat_neg[i]) { rate_error[i] = max_(rate_error[i], 0.0); }
        double i_factor = rate_error[i] / RADIANS(400.0);
        i_factor = max_(0.0, 1.0 - i_factor * i_factor);
        double rate_i = rc->rate_int[i] + i_factor * rc->gain_i[i] * rate_error[i] * dt;
        if (finite_(rate_i)) {
            rc->rate_int[i] = constrain_(rate_i, -rc->lim_int[i], rc->lim_int[i]);
        }
    }
}

void px4_rate_control_update(px4_rate_control_t *rc, const double rate[3], const double rate_sp[3],
                             const double accel[3], double dt, bool landed, double torque[3]) {
    double err[3];
    for (int i = 0; i < 3; i++) {
        err[i] = rate_sp[i] - rate[i];
        torque[i] = rc->gain_p[i] * err[i] + rc->rate_int[i] - rc->gain_d[i] * accel[i]
                    + rc->gain_ff[i] * rate_sp[i];
    }
    if (!landed) { update_integral(rc, err, dt); }
}
