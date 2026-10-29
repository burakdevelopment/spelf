#include <stdint.h>
#include <stdbool.h>
typedef struct { float x; float y; float z; } dip_t;
static const float DT = 0.1f, K = 0.05f, ALPHA = 0.3f, MMAX = 0.2f;
static bool primed = false;
static float bpx = 0.0f, bpy = 0.0f, bpz = 0.0f;
static float fx = 0.0f, fy = 0.0f, fz = 0.0f;

static float torquer(float f)
{
    const float m = 0.0f - K * f;
    return (m > MMAX) ? MMAX : ((m < -MMAX) ? -MMAX : m);
}

void f_bdot_step(float mx, float my, float mz, bool valid, dip_t *out)
{
    if (!valid) {
        primed = false;
        out->x = 0.0f;
        out->y = 0.0f;
        out->z = 0.0f;
        return;
    }
    const float bx = -my;
    const float by = mx;
    const float bz = mz;
    if (!primed) {
        bpx = bx;
        bpy = by;
        bpz = bz;
        primed = true;
        out->x = 0.0f;
        out->y = 0.0f;
        out->z = 0.0f;
        return;
    }
    fx = fx + ALPHA * ((bx - bpx) / DT - fx);
    fy = fy + ALPHA * ((by - bpy) / DT - fy);
    fz = fz + ALPHA * ((bz - bpz) / DT - fz);
    bpx = bx;
    bpy = by;
    bpz = bz;
    out->x = torquer(fx);
    out->y = torquer(fy);
    out->z = torquer(fz);
}
