#include <stdint.h>
#include <stdbool.h>
typedef struct { int32_t tag; double val; } T_Sf64;
typedef struct { int32_t tag; bool val; } T_Sb;
double now_met(void) { return 0.0; }
double now_tai(void) { return 0.0; }
double now_mono(void) { return 0.0; }
T_Sf64 read_rate(void) { T_Sf64 r = { 0, 0.0 }; return r; }
T_Sb read_contact(void) { T_Sb r = { 0, false }; return r; }
int64_t read_gyro_a(void) { return 0; }
int64_t read_gyro_b(void) { return 0; }
T_Sf64 read_gyro(void) { T_Sf64 r = { 0, 0.0 }; return r; }
T_Sf64 read_sun(void) { T_Sf64 r = { 0, 0.0 }; return r; }
void set_wheels(double t) { (void)t; }
int32_t gyro_fifo_read(void) { return 0; }
void wd_kick(void) { }
struct R_DmaBuf;
void bc_start(const struct R_DmaBuf *b) { (void)b; }
bool bc_done(void) { return false; }
uint8_t bc_wait(void) { return 0u; }
