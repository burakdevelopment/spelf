#include <stdint.h>
int32_t f_wl_crc16(const uint8_t buf[64], int32_t n)
{
    uint32_t crc = 0xFFFFu;
    for (int32_t i = 0; i < 64; ++i) {
        if (i < n) {
            crc = crc ^ ((uint32_t)buf[i] << 8);
            for (int32_t b = 0; b < 8; ++b) {
                if ((crc & 0x8000u) != 0u) {
                    crc = ((crc << 1) & 0xFFFFu) ^ 0x1021u;
                } else {
                    crc = (crc << 1) & 0xFFFFu;
                }
            }
        }
    }
    return (int32_t)crc;
}
