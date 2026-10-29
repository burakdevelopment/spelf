#include <stdint.h>
typedef struct { int32_t tag; double val; } T_Sf64;
static int calls = 0;
static double t = 0.0;
double now_mono(void) { t += 0.02; return t; }
T_Sf64 sensor(void) { T_Sf64 r; calls += 1; r.tag = (calls == 1) ? 0 : 3; r.val = 1.0; return r; }
