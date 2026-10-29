#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
const QMAX: f32 = 0.35;
const ALIM: f32 = 0.45;
const KLIM: f32 = 2.0;
const DT: f32 = 0.0125;
const RSTEP: f32 = 0.0125;
static mut XI: f32 = 0.0;
static mut DPREV: f32 = 0.0;

fn med3(a: f32, b: f32, c: f32) -> f32 {
    let lo = if a < b { a } else { b };
    let hi = if a < b { b } else { a };
    let m = if hi < c { hi } else { c };
    if lo < m { m } else { lo }
}

fn sched(qb: f32, k0: f32, k1: f32, k2: f32, k3: f32) -> f32 {
    if qb < 10000.0 {
        let w = (qb - 1000.0) / 9000.0;
        k0 + (k1 - k0) * w
    } else if qb < 30000.0 {
        let w = (qb - 10000.0) / 20000.0;
        k1 + (k2 - k1) * w
    } else {
        let w = (qb - 30000.0) / 30000.0;
        k2 + (k3 - k2) * w
    }
}

fn rate_lim(d: f32, p: f32) -> f32 {
    let up = p + RSTEP;
    let dn = p - RSTEP;
    if d > up { up } else if d < dn { dn } else { d }
}

#[no_mangle]
pub extern "C" fn f_fbw_step(stick: f32, q1: f32, q2: f32, q3: f32, a1: f32, a2: f32, a3: f32, qb: f32) -> f32 {
    let q = med3(q1, q2, q3);
    let a = med3(a1, a2, a3);
    let kq = sched(qb, 1.2, 0.6, 0.35, 0.22) * 1.0;
    let ka = sched(qb, 3.0, 1.6, 0.9, 0.55);
    let ki = sched(qb, 1.5, 0.8, 0.45, 0.3);
    let excess = if a > ALIM { a - ALIM } else { 0.0 };
    let qcmd = stick * QMAX - KLIM * excess;
    let e = qcmd - q;
    let xi = unsafe { XI };
    let raw = kq * e + ki * xi - ka * a;
    let d = if raw >= -0.44 && raw <= 0.44 {
        let xn = xi + e * DT;
        unsafe { XI = if xn > 1.0 { 1.0 } else if xn < -1.0 { -1.0 } else { xn } };
        raw
    } else if raw < -0.44 {
        -0.44
    } else {
        0.44
    };
    let lim = rate_lim(d, unsafe { DPREV });
    unsafe { DPREV = lim };
    lim
}
