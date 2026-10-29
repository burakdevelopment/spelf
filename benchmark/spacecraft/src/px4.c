#include <stdint.h>
#include <stdbool.h>
static const float GP = 0.15f, GI = 0.2f, GD = 0.003f, GFF = 0.0f, LIM = 0.3f;
static float integ[3];
float f_axis_torque(int32_t k, float err, float rate_sp, float accel)
{
    return GP * err + integ[k] - GD * accel + GFF * rate_sp;
}
void f_axis_integrate(int32_t k, float rate_error, float dt, bool sat_pos, bool sat_neg)
{
    float err = rate_error;
    if (sat_pos) {
        if (err > 0.0f) {
            err = 0.0f;
        }
    }
    if (sat_neg) {
        if (err < 0.0f) {
            err = 0.0f;
        }
    }
    const float x = err / 6.981317007977318f;
    const float q = 1.0f - x * x;
    const float i_factor = (q < 0.0f) ? 0.0f : q;
    const float rate_i = integ[k] + i_factor * GI * err * dt;
    const float lim = LIM;
    integ[k] = (rate_i > lim) ? lim : ((rate_i < 0.0f - lim) ? (0.0f - lim) : rate_i);
}
