#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}
#[no_mangle]
pub unsafe extern "C" fn f_wl_crc16(buf: *const [u8; 64], n: i32) -> i32 {
    let buf = &*buf;
    let mut crc: u32 = 0xFFFF;
    for i in 0..64 {
        if (i as i32) < n {
            crc ^= (buf[i] as u32) << 8;
            for _ in 0..8 {
                if crc & 0x8000 != 0 {
                    crc = ((crc << 1) & 0xFFFF) ^ 0x1021;
                } else {
                    crc = (crc << 1) & 0xFFFF;
                }
            }
        }
    }
    crc as i32
}
