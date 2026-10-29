#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
#[repr(C)]
pub struct Dip {
    x: f32,
    y: f32,
    z: f32,
}
const DT: f32 = 0.1;
const K: f32 = 0.05;
const ALPHA: f32 = 0.3;
const MMAX: f32 = 0.2;
struct State {
    primed: bool,
    bp: [f32; 3],
    f: [f32; 3],
}
static mut S: State = State { primed: false, bp: [0.0; 3], f: [0.0; 3] };

fn torquer(f: f32) -> f32 {
    let m = 0.0 - K * f;
    if m > MMAX { MMAX } else if m < -MMAX { -MMAX } else { m }
}

#[no_mangle]
pub extern "C" fn f_bdot_step(mx: f32, my: f32, mz: f32, valid: bool, out: &mut Dip) {
    let s = unsafe { &mut *core::ptr::addr_of_mut!(S) };
    if !valid {
        s.primed = false;
        *out = Dip { x: 0.0, y: 0.0, z: 0.0 };
        return;
    }
    let b = [-my, mx, mz];
    if !s.primed {
        s.bp = b;
        s.primed = true;
        *out = Dip { x: 0.0, y: 0.0, z: 0.0 };
        return;
    }
    s.f[0] = s.f[0] + ALPHA * ((b[0] - s.bp[0]) / DT - s.f[0]);
    s.f[1] = s.f[1] + ALPHA * ((b[1] - s.bp[1]) / DT - s.f[1]);
    s.f[2] = s.f[2] + ALPHA * ((b[2] - s.bp[2]) / DT - s.f[2]);
    s.bp = b;
    *out = Dip { x: torquer(s.f[0]), y: torquer(s.f[1]), z: torquer(s.f[2]) };
}
