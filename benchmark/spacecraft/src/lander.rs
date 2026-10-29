#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
#[repr(C)]
pub struct Leg {
    status: i32,
    value: bool,
}
#[repr(C)]
pub struct Alt {
    status: i32,
    value: f32,
}
struct Debounce {
    count: i32,
    need: i32,
    set: bool,
}
impl Debounce {
    fn feed(&mut self, c: bool) {
        if c {
            if self.count < self.need {
                self.count += 1;
            }
            if self.count >= self.need {
                self.set = true;
            }
        } else {
            self.count = 0;
        }
    }
}
const ARM_ALT: f32 = 40.0;
const HOVER: f32 = 0.38;
const KV: f32 = 0.08;
const KH: f32 = 0.1;
static mut ARMED: Debounce = Debounce { count: 0, need: 5, set: false };
static mut TOUCHDOWN: Debounce = Debounce { count: 0, need: 3, set: false };

fn leg(r: &Leg) -> i32 {
    if r.status == 0 && r.value { 1 } else { 0 }
}

#[no_mangle]
pub extern "C" fn f_lander_step(alt: Alt, vz: f32, l1: Leg, l2: Leg, l3: Leg) -> f32 {
    let armed = unsafe { &mut *core::ptr::addr_of_mut!(ARMED) };
    let touchdown = unsafe { &mut *core::ptr::addr_of_mut!(TOUCHDOWN) };
    let vref = if alt.status == 0 {
        armed.feed(alt.value < ARM_ALT);
        let v = 0.0 - KH * alt.value - 1.0;
        if v < -20.0 { -20.0 } else { v }
    } else {
        armed.feed(false);
        -2.0
    };
    let n = leg(&l1) + leg(&l2) + leg(&l3);
    touchdown.feed(armed.set && n >= 2);
    if touchdown.set {
        0.0
    } else {
        let t = HOVER + KV * (vref - vz);
        if t < 0.1 { 0.1 } else if t > 1.0 { 1.0 } else { t }
    }
}
