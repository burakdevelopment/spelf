#include <stdint.h>
int32_t f_wl_ccsds(int32_t b0, int32_t b1, int32_t b2, int32_t b3, int32_t b4, int32_t b5, int32_t rx_len)
{
    (void)b2;
    (void)b3;
    if (rx_len < 6) {
        return -1;
    }
    const int32_t w0 = b0 * 256 + b1;
    if ((w0 >> 13) != 0) {
        return -2;
    }
    if ((w0 & 2047) == 2047) {
        return -3;
    }
    const int32_t dlen = b4 * 256 + b5 + 1;
    if (6 + dlen > rx_len) {
        return -1;
    }
    return 6 + dlen;
}
