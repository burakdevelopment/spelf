import argparse
import json
import math
import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as R
import sil as S

F32 = R.F32

def clampd(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)

def interp_of(src_text, name):
    from spelfc import checker, interp, parser
    return interp.Interp(checker.check(parser.parse(src_text, name), name))

FBW_STEPS, FBW_EVERY = 1600, 8
FBW_PLANT = r"""
#include <stdint.h>
void spelf_print_f64(double v);
float f_fbw_step(float, float, float, float, float, float, float, float);
static const double ZA = -1.2, ZD = -0.1, MA = 1.5, MQ = -1.0, MD = 5.0, DT = 0.0125;
static double clampd(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }
static void deriv(double a, double q, double d, double m, double *da, double *dq) {
    *da = ZA * a + q + ZD * d;
    *dq = MA * a + MQ * q + MD * d + m;
}
int main(void) {
    double a = 0.0, q = 0.0;
    for (int32_t s = 0; s < STEPS; ++s) {
        const double t = s * DT;
        const float stick = (t >= 1.0 && t < 3.0) ? 0.5f : ((t >= 5.0 && t < 7.0) ? -0.3f
                          : ((t >= 14.0 && t < 17.0) ? 1.0f : 0.0f));
        const double m = (t >= 9.0 && t < 12.0) ? -2.1 : 0.0;
        const float qs = (float)clampd(q, -2.0, 2.0), as = (float)clampd(a, -0.6, 0.6);
        const float q3 = (t >= 4.0) ? 2.0f : qs;
        const float a1 = (t >= 16.0) ? -0.6f : as;
        const double d = (double)f_fbw_step(stick, qs, qs, q3, a1, as, as, 25000.0f);
        double k1a, k1q, k2a, k2q, k3a, k3q, k4a, k4q;
        deriv(a, q, d, m, &k1a, &k1q);
        deriv(a + 0.5 * DT * k1a, q + 0.5 * DT * k1q, d, m, &k2a, &k2q);
        deriv(a + 0.5 * DT * k2a, q + 0.5 * DT * k2q, d, m, &k3a, &k3q);
        deriv(a + DT * k3a, q + DT * k3q, d, m, &k4a, &k4q);
        a = a + DT / 6.0 * (k1a + 2.0 * k2a + 2.0 * k3a + k4a);
        q = q + DT / 6.0 * (k1q + 2.0 * k2q + 2.0 * k3q + k4q);
        if ((s + 1) % EVERY == 0) { spelf_print_f64(a); spelf_print_f64(q); spelf_print_f64(d); }
    }
    return 0;
}
""".replace("STEPS", str(FBW_STEPS)).replace("EVERY", str(FBW_EVERY))

def fbw_reference(ctrl):
    ZA, ZD, MA, MQ, MD, DT = -1.2, -0.1, 1.5, -1.0, 5.0, 0.0125
    a = q = 0.0
    out = []
    for s in range(FBW_STEPS):
        t = s * DT
        stick = 0.5 if 1.0 <= t < 3.0 else (F32(-0.3) if 5.0 <= t < 7.0 else (1.0 if 14.0 <= t < 17.0 else 0.0))
        m = -2.1 if 9.0 <= t < 12.0 else 0.0
        qs, as_ = F32(clampd(q, -2.0, 2.0)), F32(clampd(a, -0.6, 0.6))
        q3 = 2.0 if t >= 4.0 else qs
        a1 = -0.6 if t >= 16.0 else as_
        d = ctrl("fbw_step", [stick, qs, qs, q3, a1, as_, as_, 25000.0])

        def der(a, q):
            return ZA * a + q + ZD * d, MA * a + MQ * q + MD * d + m
        k1 = der(a, q)
        k2 = der(a + 0.5 * DT * k1[0], q + 0.5 * DT * k1[1])
        k3 = der(a + 0.5 * DT * k2[0], q + 0.5 * DT * k2[1])
        k4 = der(a + DT * k3[0], q + DT * k3[1])
        a = a + DT / 6.0 * (k1[0] + 2.0 * k2[0] + 2.0 * k3[0] + k4[0])
        q = q + DT / 6.0 * (k1[1] + 2.0 * k2[1] + 2.0 * k3[1] + k4[1])
        if (s + 1) % FBW_EVERY == 0:
            out += [a, q, d]
    return out

def fbw_summary(v):
    dt = FBW_EVERY * 0.0125
    t = [(i + 1) * dt for i in range(len(v) // 3)]
    a, q, d = v[0::3], v[1::3], v[2::3]
    post = [(x, y) for tt, x, y in zip(t, a, q) if 12.0 <= tt < 14.0]
    sat = sum(1 for x in d if abs(x) >= 0.4399) * dt
    return {"t": t, "alpha": a, "q": q, "defl": d,
            "max_alpha_deg": math.degrees(max(abs(x) for x in a)),
            "post_gust_alpha_overshoot_deg": math.degrees(max(x for x, _ in post)),
            "post_gust_q_peak_dps": math.degrees(max(y for _, y in post)),
            "saturated_s": round(sat, 3),
            "departed": any(abs(x) >= 0.6 for x in a)}

BD_STEPS, BD_EVERY = 36000, 100
BD_J = (0.033, 0.035, 0.007)
BD_DTH = 2.0 * (2.0 * math.pi / 5400.0) * 0.1
BD_C, BD_S = math.cos(BD_DTH), math.sin(BD_DTH)
BD_PLANT = r"""
#include <stdint.h>
#include <stdbool.h>
void spelf_print_f64(double v);
typedef struct { float x; float y; float z; } D;
void f_bdot_step(float, float, float, bool, D *);
static const double J0 = J0V, J1 = J1V, J2 = J2V, DT = 0.1, CR = CRV, SR = SRV;
static double clampd(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }
static void deriv(const double q[4], const double w[3], const double t[3], double dq[4], double dw[3]) {
    const double hx = J0 * w[0], hy = J1 * w[1], hz = J2 * w[2];
    const double cx = w[1] * hz - w[2] * hy, cy = w[2] * hx - w[0] * hz, cz = w[0] * hy - w[1] * hx;
    dw[0] = (t[0] - cx) / J0;
    dw[1] = (t[1] - cy) / J1;
    dw[2] = (t[2] - cz) / J2;
    dq[0] = 0.5 * (0.0 - q[1] * w[0] - q[2] * w[1] - q[3] * w[2]);
    dq[1] = 0.5 * (q[0] * w[0] + q[2] * w[2] - q[3] * w[1]);
    dq[2] = 0.5 * (q[0] * w[1] + q[3] * w[0] - q[1] * w[2]);
    dq[3] = 0.5 * (q[0] * w[2] + q[1] * w[1] - q[2] * w[0]);
}
int main(void) {
    double q[4] = { 1.0, 0.0, 0.0, 0.0 }, w[3] = { 0.15, -0.1, 0.2 }, bi[3] = { 30e-6, 0.0, 25e-6 };
    for (int32_t s = 0; s < STEPS; ++s) {
        const double r00 = 1.0 - 2.0 * (q[2] * q[2] + q[3] * q[3]), r01 = 2.0 * (q[1] * q[2] - q[0] * q[3]);
        const double r02 = 2.0 * (q[1] * q[3] + q[0] * q[2]), r10 = 2.0 * (q[1] * q[2] + q[0] * q[3]);
        const double r11 = 1.0 - 2.0 * (q[1] * q[1] + q[3] * q[3]), r12 = 2.0 * (q[2] * q[3] - q[0] * q[1]);
        const double r20 = 2.0 * (q[1] * q[3] - q[0] * q[2]), r21 = 2.0 * (q[2] * q[3] + q[0] * q[1]);
        const double r22 = 1.0 - 2.0 * (q[1] * q[1] + q[2] * q[2]);
        double b[3];
        b[0] = r00 * bi[0] + r10 * bi[1] + r20 * bi[2];
        b[1] = r01 * bi[0] + r11 * bi[1] + r21 * bi[2];
        b[2] = r02 * bi[0] + r12 * bi[1] + r22 * bi[2];
        const float mx = (float)clampd(b[1] * 1e6, -80.0, 80.0);
        const float my = (float)clampd(-b[0] * 1e6, -80.0, 80.0);
        const float mz = (float)clampd(b[2] * 1e6, -80.0, 80.0);
        D dp;
        f_bdot_step(mx, my, mz, (s % 200) != 0, &dp);
        const double m0 = (double)dp.x, m1 = (double)dp.y, m2 = (double)dp.z;
        double t[3];
        t[0] = m1 * b[2] - m2 * b[1];
        t[1] = m2 * b[0] - m0 * b[2];
        t[2] = m0 * b[1] - m1 * b[0];
        double k1q[4], k1w[3], k2q[4], k2w[3], k3q[4], k3w[3], k4q[4], k4w[3], qa[4], wa[3];
        deriv(q, w, t, k1q, k1w);
        for (int i = 0; i < 4; ++i) { qa[i] = q[i] + 0.5 * DT * k1q[i]; }
        for (int i = 0; i < 3; ++i) { wa[i] = w[i] + 0.5 * DT * k1w[i]; }
        deriv(qa, wa, t, k2q, k2w);
        for (int i = 0; i < 4; ++i) { qa[i] = q[i] + 0.5 * DT * k2q[i]; }
        for (int i = 0; i < 3; ++i) { wa[i] = w[i] + 0.5 * DT * k2w[i]; }
        deriv(qa, wa, t, k3q, k3w);
        for (int i = 0; i < 4; ++i) { qa[i] = q[i] + DT * k3q[i]; }
        for (int i = 0; i < 3; ++i) { wa[i] = w[i] + DT * k3w[i]; }
        deriv(qa, wa, t, k4q, k4w);
        for (int i = 0; i < 4; ++i) { q[i] = q[i] + DT / 6.0 * (k1q[i] + 2.0 * k2q[i] + 2.0 * k3q[i] + k4q[i]); }
        for (int i = 0; i < 3; ++i) { w[i] = w[i] + DT / 6.0 * (k1w[i] + 2.0 * k2w[i] + 2.0 * k3w[i] + k4w[i]); }
        const double n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3];
        const double sc = (3.0 - n2) * 0.5;
        for (int i = 0; i < 4; ++i) { q[i] = q[i] * sc; }
        const double b0 = CR * bi[0] - SR * bi[1], b1 = SR * bi[0] + CR * bi[1];
        bi[0] = b0;
        bi[1] = b1;
        if ((s + 1) % EVERY == 0) { spelf_print_f64(w[0]); spelf_print_f64(w[1]); spelf_print_f64(w[2]); }
    }
    return 0;
}
""".replace("STEPS", str(BD_STEPS)).replace("EVERY", str(BD_EVERY)).replace("J0V", repr(BD_J[0])) \
   .replace("J1V", repr(BD_J[1])).replace("J2V", repr(BD_J[2])).replace("CRV", repr(BD_C)).replace("SRV", repr(BD_S))

def bdot_reference(ctrl):
    J, DT = BD_J, 0.1
    q, w, bi = [1.0, 0.0, 0.0, 0.0], [0.15, -0.1, 0.2], [30e-6, 0.0, 25e-6]
    out = []

    def deriv(q, w, t):
        hx, hy, hz = J[0] * w[0], J[1] * w[1], J[2] * w[2]
        cx, cy, cz = w[1] * hz - w[2] * hy, w[2] * hx - w[0] * hz, w[0] * hy - w[1] * hx
        dw = [(t[0] - cx) / J[0], (t[1] - cy) / J[1], (t[2] - cz) / J[2]]
        dq = [0.5 * (0.0 - q[1] * w[0] - q[2] * w[1] - q[3] * w[2]), 0.5 * (q[0] * w[0] + q[2] * w[2] - q[3] * w[1]),
              0.5 * (q[0] * w[1] + q[3] * w[0] - q[1] * w[2]), 0.5 * (q[0] * w[2] + q[1] * w[1] - q[2] * w[0])]
        return dq, dw
    for s in range(BD_STEPS):
        r00, r01 = 1.0 - 2.0 * (q[2] * q[2] + q[3] * q[3]), 2.0 * (q[1] * q[2] - q[0] * q[3])
        r02, r10 = 2.0 * (q[1] * q[3] + q[0] * q[2]), 2.0 * (q[1] * q[2] + q[0] * q[3])
        r11, r12 = 1.0 - 2.0 * (q[1] * q[1] + q[3] * q[3]), 2.0 * (q[2] * q[3] - q[0] * q[1])
        r20, r21 = 2.0 * (q[1] * q[3] - q[0] * q[2]), 2.0 * (q[2] * q[3] + q[0] * q[1])
        r22 = 1.0 - 2.0 * (q[1] * q[1] + q[2] * q[2])
        b = [r00 * bi[0] + r10 * bi[1] + r20 * bi[2], r01 * bi[0] + r11 * bi[1] + r21 * bi[2],
             r02 * bi[0] + r12 * bi[1] + r22 * bi[2]]
        mx, my, mz = (F32(clampd(b[1] * 1e6, -80.0, 80.0)), F32(clampd(-b[0] * 1e6, -80.0, 80.0)),
                      F32(clampd(b[2] * 1e6, -80.0, 80.0)))
        dp = ctrl("bdot_step", [mx, my, mz, s % 200 != 0])
        m0, m1, m2 = dp["x"], dp["y"], dp["z"]
        t = [m1 * b[2] - m2 * b[1], m2 * b[0] - m0 * b[2], m0 * b[1] - m1 * b[0]]
        k1q, k1w = deriv(q, w, t)
        k2q, k2w = deriv([q[i] + 0.5 * DT * k1q[i] for i in range(4)], [w[i] + 0.5 * DT * k1w[i] for i in range(3)], t)
        k3q, k3w = deriv([q[i] + 0.5 * DT * k2q[i] for i in range(4)], [w[i] + 0.5 * DT * k2w[i] for i in range(3)], t)
        k4q, k4w = deriv([q[i] + DT * k3q[i] for i in range(4)], [w[i] + DT * k3w[i] for i in range(3)], t)
        q = [q[i] + DT / 6.0 * (k1q[i] + 2.0 * k2q[i] + 2.0 * k3q[i] + k4q[i]) for i in range(4)]
        w = [w[i] + DT / 6.0 * (k1w[i] + 2.0 * k2w[i] + 2.0 * k3w[i] + k4w[i]) for i in range(3)]
        n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
        sc = (3.0 - n2) * 0.5
        q = [x * sc for x in q]
        bi = [BD_C * bi[0] - BD_S * bi[1], BD_S * bi[0] + BD_C * bi[1], bi[2]]
        if (s + 1) % BD_EVERY == 0:
            out += w
    return out

def bdot_summary(v):
    dt = BD_EVERY * 0.1
    rate = [math.degrees(math.sqrt(v[i] ** 2 + v[i + 1] ** 2 + v[i + 2] ** 2)) for i in range(0, len(v), 3)]
    t = [(i + 1) * dt for i in range(len(rate))]
    below = next((tt for tt, r in zip(t, rate) if r < 1.0), None)
    return {"t": t, "rate_dps": rate, "final_rate_dps": rate[-1], "t_below_1dps": below}

LD_STEPS, LD_EVERY = 4000, 10
LD_PLANT = r"""
#include <stdint.h>
#include <stdbool.h>
void spelf_print_f64(double v);
typedef struct { int32_t s; bool v; } L;
typedef struct { int32_t s; float v; } H;
float f_lander_step(H, float, L, L, L);
static double clampd(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }
int main(void) {
    const double g = 3.711, HOVER = 0.38, DT = 0.01;
    double h = 400.0, v = -30.0, td_step = -1.0, td_v = 0.0, cut_alt = -1.0;
    for (int32_t s = 0; s < STEPS; ++s) {
        H alt;
        if (s >= 900 && s < 912) { alt.s = 3; alt.v = 0.0f; }
        else { alt.s = 0; alt.v = (float)clampd(h, 0.0, 2500.0); }
        L l1 = { 0, false }, l2 = { 0, false }, l3 = { 0, false };
        if (s == 200 || s == 201) { l1.v = true; l2.v = true; l3.v = true; }
        if (s >= 1300 && s < 1304) { l1.v = true; l2.v = true; }
        if (h <= 0.0) { l1.v = true; l2.s = 3; l2.v = false; l3.v = true; }
        const double thr = (double)f_lander_step(alt, (float)clampd(v, -80.0, 10.0), l1, l2, l3);
        if (thr == 0.0 && cut_alt < 0.0) { cut_alt = h; }
        if (h > 0.0) {
            v = v + (thr * (g / HOVER) - g) * DT;
            h = h + v * DT;
            if (h <= 0.0) { td_step = (double)s; td_v = v; h = 0.0; v = 0.0; }
        }
        if ((s + 1) % EVERY == 0) { spelf_print_f64(h); spelf_print_f64(v); spelf_print_f64(thr); }
    }
    spelf_print_f64(td_step); spelf_print_f64(td_v); spelf_print_f64(cut_alt);
    return 0;
}
""".replace("STEPS", str(LD_STEPS)).replace("EVERY", str(LD_EVERY))

def lander_reference(ctrl):
    g, HOVER, DT = 3.711, 0.38, 0.01
    h, v, td_step, td_v, cut_alt = 400.0, -30.0, -1.0, 0.0, -1.0
    out = []
    for s in range(LD_STEPS):
        alt = (3, 0.0) if 900 <= s < 912 else (0, F32(clampd(h, 0.0, 2500.0)))
        l1, l2, l3 = [0, False], [0, False], [0, False]
        if s in (200, 201):
            l1[1] = l2[1] = l3[1] = True
        if 1300 <= s < 1304:
            l1[1] = l2[1] = True
        if h <= 0.0:
            l1, l2, l3 = [0, True], [3, False], [0, True]
        thr = ctrl("lander_step", [alt, F32(clampd(v, -80.0, 10.0)), tuple(l1), tuple(l2), tuple(l3)])
        if thr == 0.0 and cut_alt < 0.0:
            cut_alt = h
        if h > 0.0:
            v = v + (thr * (g / HOVER) - g) * DT
            h = h + v * DT
            if h <= 0.0:
                td_step, td_v, h, v = float(s), v, 0.0, 0.0
        if (s + 1) % LD_EVERY == 0:
            out += [h, v, thr]
    return out + [td_step, td_v, cut_alt]

def lander_summary(v):
    body, (td_step, td_v, cut_alt) = v[:-3], v[-3:]
    dt = LD_EVERY * 0.01
    return {"t": [(i + 1) * dt for i in range(len(body) // 3)], "h": body[0::3], "v": body[1::3],
            "throttle": body[2::3], "touchdown_t": td_step * 0.01 if td_step >= 0 else None,
            "touchdown_v": td_v, "engine_cut_alt": cut_alt,
            "outcome": "soft landing" if td_step >= 0 and abs(td_v) < 3.0 and cut_alt <= 0.5 else "CRASH"}

SCEN = {
    "fbw": {"wl": "fbw", "plant": FBW_PLANT, "ref": fbw_reference, "sum": fbw_summary},
    "bdot": {"wl": "bdot", "plant": BD_PLANT, "ref": bdot_reference, "sum": bdot_summary},
    "lander": {"wl": "lander", "plant": LD_PLANT, "ref": lander_reference, "sum": lander_summary},
}

def _between(start, end_incl):
    def f(text, new):
        i = text.index(start)
        j = text.index(end_incl, i) + len(end_incl)
        return text[:i] + new + text[j:]
    return f

MUTANTS = [
    {"id": "FBW-M1", "scen": "fbw", "class": "algorithm (no language rule)",
     "title": "Mean instead of median in triplex gyro / angle-of-attack voting",
     "c": [("    const float lo = (a < b) ? a : b;\n    const float hi = (a < b) ? b : a;\n"
            "    const float m = (hi < c) ? hi : c;\n    return (lo < m) ? m : lo;", "    return (a + b + c) / 3.0f;")],
     "rs": [("    let lo = if a < b { a } else { b };\n    let hi = if a < b { b } else { a };\n"
             "    let m = if hi < c { hi } else { c };\n    if lo < m { m } else { lo }", "    (a + b + c) / 3.0")],
     "adb": [(_between("   function Med3 (A, B, C : Rate)", "end Med3;"),
              "   function Med3 (A, B, C : Rate) return Rate is ((A + B + C) / 3.0);"),
             (_between("   function Med3a (A, B, C : Aoa)", "end Med3a;"),
              "   function Med3a (A, B, C : Aoa) return Aoa is ((A + B + C) / 3.0);")],
     "spelf": [(_between("fn med3(a: Rate", "\n}"), "fn med3(a: Rate, b: Rate, c: Rate) -> Rate {\n  (a + b + c) / 3.0\n}"),
               (_between("fn med3a(a: Aoa", "\n}"), "fn med3a(a: Aoa, b: Aoa, c: Aoa) -> Aoa {\n  (a + b + c) / 3.0\n}")],
     "spelf_expect": None},
    {"id": "FBW-M2", "scen": "fbw", "class": "integrator windup (R-407)",
     "title": "Integrator not frozen when the elevator saturates (no anti-windup)",
     "c": [("    if ((raw >= -0.44f) && (raw <= 0.44f)) {\n        d = raw;\n        const float xn = xi + e * DT;\n"
            "        xi = (xn > 1.0f) ? 1.0f : ((xn < -1.0f) ? -1.0f : xn);\n    } else {\n"
            "        d = (raw < -0.44f) ? -0.44f : 0.44f;\n    }",
            "    const float xn = xi + e * DT;\n    xi = (xn > 1.0f) ? 1.0f : ((xn < -1.0f) ? -1.0f : xn);\n"
            "    d = (raw < -0.44f) ? -0.44f : ((raw > 0.44f) ? 0.44f : raw);")],
     "rs": [("    let d = if raw >= -0.44 && raw <= 0.44 {\n        let xn = xi + e * DT;\n"
             "        unsafe { XI = if xn > 1.0 { 1.0 } else if xn < -1.0 { -1.0 } else { xn } };\n        raw\n"
             "    } else if raw < -0.44 {\n        -0.44\n    } else {\n        0.44\n    };",
             "    let xn = xi + e * DT;\n    unsafe { XI = if xn > 1.0 { 1.0 } else if xn < -1.0 { -1.0 } else { xn } };\n"
             "    let d = if raw < -0.44 { -0.44 } else if raw > 0.44 { 0.44 } else { raw };")],
     "adb": [(_between("      if Raw >= -0.44 and then Raw <= 0.44 then", "      end if;\n      Lim"),
              "      Xn := Xi + E * DT;\n      Xi := (if Xn > 1.0 then 1.0 elsif Xn < -1.0 then -1.0 else Xn);\n"
              "      D := (if Raw < -0.44 then Defl'First elsif Raw > 0.44 then Defl'Last else Raw);\n      Lim")],
     "spelf": [(_between("  match saturate(raw", "\n  }\n}"),
                "  let d = saturate(raw, -0.44[rad], 0.44[rad]);\n  let xn = xi + e * DT;\n"
                "  xi = if xn > 1.0[rad] { 1.0[rad] } else { if xn < -1.0[rad] { -1.0[rad] } else { xn } };\n"
                "  let lim = rate_lim(d, dprev);\n  dprev = lim;\n  lim\n}")],
     "spelf_expect": "R-407"},
    {"id": "BDOT-M1", "scen": "bdot", "class": "frame (05 §5.2)",
     "title": "Magnetometer reading used without rotation to the body frame",
     "c": [("    const float bx = -my;\n    const float by = mx;", "    const float bx = mx;\n    const float by = my;")],
     "rs": [("let b = [-my, mx, mz];", "let b = [mx, my, mz];")],
     "adb": [("      Bx : constant Bmag := -My;\n      By : constant Bmag := Mx;",
              "      Bx : constant Bmag := Mx;\n      By : constant Bmag := My;")],
     "spelf": [("rotate<BodyV>(MAG_TO_BODY, MagV { x: mx, y: my, z: mz })", "BodyV { x: mx, y: my, z: mz }")],
     "spelf_expect": "05§5.2"},
    {"id": "LND-M1", "scen": "lander", "class": "latched transient (R-404)",
     "title": "Mars Polar Lander: transient leg contact written straight to a 'touchdown' flag",
     "c": [("    debounce(&touchdown, armed.set && (n >= 2));", "    if (n >= 2) {\n        touchdown.set = true;\n    }")],
     "rs": [("    touchdown.feed(armed.set && n >= 2);", "    if n >= 2 {\n        touchdown.set = true;\n    }")],
     "adb": [("      Feed (Touchdown, Armed.Set and then N >= 2);",
              "      if N >= 2 then\n         Touchdown.Set := True;\n      end if;")],
     "spelf": [("state touchdown: Latch = latch(persists_for = 3);", "state touchdown: bool = false;"),
               ("  feed(touchdown, is_set(armed) && n >= 2);", "  if n >= 2 { touchdown = true; }"),
               ("  if is_set(touchdown) {", "  if touchdown {")],
     "spelf_expect": "R-404"},
    {"id": "LND-M2", "scen": "lander", "class": "sensor status ignored (R-402)",
     "title": "Schiaparelli lesson: the value of an invalid radar reading used without checking its status",
     "c": [("    if (alt.status == 0) {", "    if (alt.value >= 0.0f) {")],
     "rs": [("    let vref = if alt.status == 0 {", "    let vref = if alt.value >= 0.0 {")],
     "adb": [("      if Alt.S = 0 then", "      if Alt.V >= 0.0 then")],
     "spelf": [(_between("  match alt {\n    Valid(h) => feed", "    _ => feed(armed, false),\n  }"),
                "  feed(armed, alt < ARM_ALT);")],
     "spelf_expect": "R-402"},
]

def apply(text, edits):
    for old, new in edits:
        if callable(old):
            text = old(text, new)
        else:
            if text.count(old) != 1:
                raise ValueError(f"a mutant edit must match exactly once: {old[:60]!r} ({text.count(old)} times)")
            text = text.replace(old, new)
    return text

def link_plant(plant_src, target, objs, extra, work: pathlib.Path):
    t = R.TGT[target]
    work.mkdir(parents=True, exist_ok=True)
    (work / "plant.c").write_text(plant_src, encoding="utf-8")
    rt = R.XV.HARNESS.replace("void *memcpy(", "__attribute__((weak)) void *memcpy(") \
                     .replace("void *memset(", "__attribute__((weak)) void *memset(")
    (work / "rt.c").write_text(rt + R.MARK, encoding="utf-8")
    if target == "leon3":
        o = []
        for name in ("plant", "rt"):
            R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-c", work / f"{name}.c", "-o", work / f"{name}.o"])
            o.append(work / f"{name}.o")
        (work / "srt.c").write_text(R.XV.SPARC_RT, encoding="utf-8")
        R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-c", work / "srt.c", "-o", work / "srt.o"])
        return R.minild.link([p.read_bytes() for p in objs + o + [work / "srt.o"]])
    out = work / "sil.elf"
    R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-Wl,-e,main", "-Wl,--gc-sections", work / "plant.c", work / "rt.c",
           *objs, *extra, "-o", out])
    return R.XV.parse_elf(out.read_bytes())

def run_c_controller(c_src, plant, target, work: pathlib.Path):
    t = R.TGT[target]
    work.mkdir(parents=True, exist_ok=True)
    (work / "m.c").write_text(c_src, encoding="utf-8")
    R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-c", work / "m.c", "-o", work / "m.o"])
    return S.emulate_fast(link_plant(plant, target, [work / "m.o"], [], work), target)

def bits_equal(a, b):
    return len(a) == len(b) and all(struct.pack("<d", x) == struct.pack("<d", y) for x, y in zip(a, b))

def multilang_tools():
    import importlib.util
    if "multilang_run" not in sys.modules:
        ml_dir = R.ROOT / "benchmark" / "multilang"
        sys.path.insert(0, str(ml_dir))
        spec = importlib.util.spec_from_file_location("multilang_run", ml_dir / "run.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["multilang_run"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["multilang_run"]

def static_verdicts(m, work: pathlib.Path):
    ML = multilang_tools()
    wl = SCEN[m["scen"]]["wl"]
    unit = R.ADA_UNIT[wl]
    out = {}
    base_c = (R.SRC / f"{wl}.c").read_text(encoding="utf-8")
    base_rs = (R.SRC / f"{wl}.rs").read_text(encoding="utf-8")
    ads = (R.SRC / f"{unit}.ads").read_text(encoding="utf-8")
    base_adb = (R.SRC / f"{unit}.adb").read_text(encoding="utf-8")

    def fresh(name):
        d = work / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def newmsgs(fn, base, mut, tag):
        okb, mb = fn(base, fresh(tag + "_b"))
        okm, mm = fn(mut, fresh(tag + "_m"))
        if okm is None:
            return {"tool": "missing"}
        extra = sorted(ML.norm(mm) - ML.norm(mb))
        return {"accepted": bool(okm), "new_diagnostics": extra[:4], "n_new": len(extra)}
    out["C: GCC -fanalyzer"] = newmsgs(ML.c_static, base_c, apply(base_c, m["c"]), "gcc")
    out["C: Cppcheck MISRA"] = newmsgs(ML.misra_static, base_c, apply(base_c, m["c"]), "misra")

    def clippy(src, d):
        f = d / "lib.rs"
        f.write_text(src, encoding="utf-8")
        rc, txt = ML.sh([str(ML.CARGO / "clippy-driver.exe"), "--edition", "2021", "--crate-type", "lib",
                         "--target", "thumbv7r-none-eabihf", "-W", "clippy::pedantic", "-W", "clippy::all",
                         "--emit=metadata", "-o", str(d / "lib.rmeta"), str(f)])
        msgs = [l for l in txt.splitlines() if (l.startswith("warning") or l.startswith("error"))
                and "generated" not in l]
        return rc == 0, msgs
    out["Rust: rustc + Clippy"] = newmsgs(clippy, base_rs, apply(base_rs, m["rs"]), "clippy")

    def gnatprove(adb, d):
        files = {f"{unit}.ads": ads, f"{unit}.adb": adb,
                 "main.adb": f"with {unit.title()};\nprocedure Main with SPARK_Mode => Off is\nbegin\n   null;\nend Main;\n"}
        ok, msgs = ML.spark_static(files, d)
        return ok, msgs
    gp = newmsgs(gnatprove, base_adb, apply(base_adb, m["adb"]), "gnatprove")
    gp["proved_clean"] = gp.get("accepted") and gp.get("n_new", 1) == 0
    out["Ada: GNATprove (AoRTE)"] = gp
    return out

def spelf_verdict(m):
    from spelfc import checker, parser
    from spelfc.diagnostics import SpelfError
    wl = SCEN[m["scen"]]["wl"]
    src = apply((R.SRC / f"{wl}.spelf").read_text(encoding="utf-8"), m["spelf"])
    try:
        checker.check(parser.parse(src, f"{wl}_{m['id']}.spelf"), f"{wl}_{m['id']}.spelf")
        return {"accepted": True, "rule": None, "message": "accepted"}
    except SpelfError as e:
        return {"accepted": False, "rule": e.rule, "message": e.message}

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="fbw,bdot,lander")
    ap.add_argument("--targets", default="leon3,rv32,r5")
    ap.add_argument("--variants", default=",".join(R.VARIANTS))
    ap.add_argument("--no-static", action="store_true")
    a = ap.parse_args()
    out_json = HERE / "sil_complex_results.json"
    res = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
    for sc in a.scenarios.split(","):
        spec = SCEN[sc]
        src = (R.SRC / f"{spec['wl']}.spelf").read_text(encoding="utf-8")
        it = interp_of(src, f"{spec['wl']}.spelf")
        ref = spec["ref"](it.call)
        rsum = spec["sum"](ref)
        print(f"[{sc}] reference (Python plant + spelfc interpreter): "
              + ", ".join(f"{k}={v}" for k, v in rsum.items() if not isinstance(v, list)))
        r = res.setdefault(sc, {})
        r["reference"] = rsum
        r["cells"] = {}
        for target in a.targets.split(","):
            for v in a.variants.split(","):
                key = f"{v} | {target}"
                with tempfile.TemporaryDirectory() as d:
                    work = pathlib.Path(d)
                    try:
                        b = R.build_variant(spec["wl"], v, target, work / "b")
                        if b is None:
                            r["cells"][key] = {"status": "—"}
                            continue
                        objs, _, extra = b
                        got = S.emulate_fast(link_plant(spec["plant"], target, objs, extra, work / "b"), target)
                    except Exception as ex:
                        r["cells"][key] = {"status": "error", "error": str(ex)[-500:]}
                        print(f"[{sc}] {key}: ERROR {str(ex)[-300:]}")
                        continue
                same = bits_equal(got, ref)
                r["cells"][key] = {"status": "ok", "bit_identical": same}
                print(f"[{sc}] {key:22s} bit-identical to the reference: {same}")
        r["mutants"] = {}
        for m in (mm for mm in MUTANTS if mm["scen"] == sc):
            c_src = apply((R.SRC / f"{spec['wl']}.c").read_text(encoding="utf-8"), m["c"])
            with tempfile.TemporaryDirectory() as d:
                got = run_c_controller(c_src, spec["plant"], "rv32", pathlib.Path(d))
            msum = spec["sum"](got)
            entry = {"title": m["title"], "class": m["class"], "c_closed_loop": msum, "spelf": spelf_verdict(m),
                     "spelf_expected": m["spelf_expect"]}
            if not a.no_static:
                with tempfile.TemporaryDirectory() as d:
                    entry["static"] = static_verdicts(m, pathlib.Path(d))
            r["mutants"][m["id"]] = entry
            brief = {k: v for k, v in msum.items() if not isinstance(v, list)}
            print(f"[{sc}] {m['id']} {m['title']}\n    C closed loop (RV32): {brief}\n"
                  f"    Spelf: {'ACCEPTED' if entry['spelf']['accepted'] else 'REJECTED [' + entry['spelf']['rule'] + ']'}"
                  f" (expected: {m['spelf_expect'] or 'accepted, algorithm error'})")
            for tool, vv in entry.get("static", {}).items():
                print(f"    {tool}: {vv}")
        out_json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
