#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
const KP: f64 = 0.02;
const KD: f64 = 0.15;
const TMAX: f64 = 0.01;
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub extern "C" fn f_wl_adcs(k: i32, qw: f64, qx: f64, qy: f64, qz: f64, rw: f64, rx: f64, ry: f64, rz: f64,
                            wx: f64, wy: f64, wz: f64) -> f64 {
    let ew = rw * qw + rx * qx + ry * qy + rz * qz;
    let ex = rw * qx - rx * qw - ry * qz + rz * qy;
    let ey = rw * qy + rx * qz - ry * qw - rz * qx;
    let ez = rw * qz - rx * qy + ry * qx - rz * qw;
    let e0 = if k == 0 { ex } else if k == 1 { ey } else { ez };
    let w0 = if k == 0 { wx } else if k == 1 { wy } else { wz };
    let e = if ew < 0.0 { 0.0 - e0 } else { e0 };
    let t = 0.0 - KP * e - KD * w0;
    if t < -TMAX {
        -TMAX
    } else if t > TMAX {
        TMAX
    } else {
        t
    }
}
