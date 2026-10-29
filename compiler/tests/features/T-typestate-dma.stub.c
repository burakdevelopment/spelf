#include <stdint.h>
struct R_DmaBuf;
typedef struct { uint16_t a[32]; } T_A32ui16_stub;
struct R_DmaBuf_view { T_A32ui16_stub words; uint8_t count; };
void bc_start(const struct R_DmaBuf *b) {
    struct R_DmaBuf_view *v = (struct R_DmaBuf_view *)(void *)b;
    v->words.a[1] = (uint16_t)(v->words.a[0] + 1u);
}
uint8_t bc_wait(void) { return 0u; }
