#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
const GP: f32 = 0.15;
const GI: f32 = 0.2;
const GD: f32 = 0.003;
const GFF: f32 = 0.0;
const LIM: f32 = 0.3;
static mut INTEG: [f32; 3] = [0.0; 3];
#[no_mangle]
pub extern "C" fn f_axis_torque(k: i32, err: f32, rate_sp: f32, accel: f32) -> f32 {
    let i = unsafe { INTEG[k as usize] };
    GP * err + i - GD * accel + GFF * rate_sp
}
#[no_mangle]
pub extern "C" fn f_axis_integrate(k: i32, rate_error: f32, dt: f32, sat_pos: bool, sat_neg: bool) {
    let mut err = rate_error;
    if sat_pos && err > 0.0 {
        err = 0.0;
    }
    if sat_neg && err < 0.0 {
        err = 0.0;
    }
    let x = err / 6.981_317_007_977_318;
    let q = 1.0 - x * x;
    let i_factor = if q < 0.0 { 0.0 } else { q };
    let rate_i = unsafe { INTEG[k as usize] } + i_factor * GI * err * dt;
    let lim = LIM;
    let v = if rate_i > lim { lim } else if rate_i < 0.0 - lim { 0.0 - lim } else { rate_i };
    unsafe { INTEG[k as usize] = v };
}
