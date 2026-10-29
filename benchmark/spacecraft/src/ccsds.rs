#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
#[no_mangle]
pub extern "C" fn f_wl_ccsds(b0: i32, b1: i32, _b2: i32, _b3: i32, b4: i32, b5: i32, rx_len: i32) -> i32 {
    if rx_len < 6 {
        return -1;
    }
    let w0 = b0 * 256 + b1;
    if (w0 >> 13) != 0 {
        return -2;
    }
    if (w0 & 2047) == 2047 {
        return -3;
    }
    let dlen = b4 * 256 + b5 + 1;
    if 6 + dlen > rx_len {
        return -1;
    }
    6 + dlen
}
