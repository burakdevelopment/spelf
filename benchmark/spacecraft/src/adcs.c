#include <stdint.h>
#define KP 0.02
#define KD 0.15
#define TMAX 0.01
double f_wl_adcs(int32_t k, double qw, double qx, double qy, double qz, double rw, double rx, double ry,
                 double rz, double wx, double wy, double wz)
{
    const double ew = rw * qw + rx * qx + ry * qy + rz * qz;
    const double ex = rw * qx - rx * qw - ry * qz + rz * qy;
    const double ey = rw * qy + rx * qz - ry * qw - rz * qx;
    const double ez = rw * qz - rx * qy + ry * qx - rz * qw;
    const double e0 = (k == 0) ? ex : ((k == 1) ? ey : ez);
    const double w0 = (k == 0) ? wx : ((k == 1) ? wy : wz);
    const double e = (ew < 0.0) ? (0.0 - e0) : e0;
    const double t = 0.0 - KP * e - KD * w0;
    double r = t;
    if (t < -TMAX) {
        r = -TMAX;
    } else if (t > TMAX) {
        r = TMAX;
    } else {
        r = t;
    }
    return r;
}
