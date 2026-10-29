from programs_a import ada

P = {}

P["M-19"] = {
    "title": "Reuse outside the validated envelope (Ariane 501 root cause)",
    "c": {"mutant": r'''#include <assert.h>
static double inertial_reference(double v_h) { assert(v_h <= 4000.0); return v_h; }
static double ariane5_flight(double v_h) { return inertial_reference(v_h); }
int main(void) { volatile double v = 5000.0; volatile double r = ariane5_flight(v); (void)r; return 0; }
''', "fixed": r'''#include <assert.h>
static double inertial_reference(double v_h) { assert(v_h <= 4000.0); return v_h; }
static double inertial_reference_a5(double v_h) { return v_h; }
static double ariane5_flight(double v_h) { return inertial_reference_a5(v_h); }
int main(void) { volatile double v = 5000.0; volatile double r = ariane5_flight(v) + inertial_reference(1.0); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   subtype V_A5 is Long_Float range 0.0 .. 6800.0;\n"
                          "   function Inertial_Reference (V_H : Long_Float) return Long_Float\n"
                          "     with Pre => V_H in 0.0 .. 4000.0;\n"
                          "   function Ariane5_Flight (V_H : V_A5) return Long_Float;\n",
                          "   function Inertial_Reference (V_H : Long_Float) return Long_Float is (V_H);\n"
                          "   function Ariane5_Flight (V_H : V_A5) return Long_Float is (Inertial_Reference (V_H));\n",
                          "   R : Long_Float;\n", "   R := Ariane5_Flight (5000.0 + Long_Float (K));\n   if R = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype V_A5 is Long_Float range 0.0 .. 6800.0;\n"
                         "   function Inertial_Reference (V_H : Long_Float) return Long_Float\n"
                         "     with Pre => V_H in 0.0 .. 4000.0;\n"
                         "   function Inertial_Reference_A5 (V_H : V_A5) return Long_Float;\n"
                         "   function Ariane5_Flight (V_H : V_A5) return Long_Float;\n",
                         "   function Inertial_Reference (V_H : Long_Float) return Long_Float is (V_H);\n"
                         "   function Inertial_Reference_A5 (V_H : V_A5) return Long_Float is (V_H);\n"
                         "   function Ariane5_Flight (V_H : V_A5) return Long_Float is (Inertial_Reference_A5 (V_H));\n",
                         "   R : Long_Float;\n", "   R := Ariane5_Flight (5000.0 + Long_Float (K)) + Inertial_Reference (1.0);\n   if R = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn inertial_reference(v_h: f64) -> f64 { debug_assert!(v_h <= 4000.0, "Ariane 4 envelope"); v_h }
fn ariane5_flight(v_h: f64) -> f64 { inertial_reference(v_h) }
fn main() { println!("{}", ariane5_flight(std::hint::black_box(5000.0))); }
''', "fixed": r'''fn inertial_reference(v_h: f64) -> f64 { debug_assert!(v_h <= 4000.0, "Ariane 4 envelope"); v_h }
fn inertial_reference_a5(v_h: f64) -> f64 { v_h }
fn ariane5_flight(v_h: f64) -> f64 { inertial_reference_a5(v_h) }
fn main() { println!("{}", ariane5_flight(std::hint::black_box(5000.0)) + inertial_reference(1.0)); }
'''},
    "declares": {"c": True, "ada": True, "rust": True},
}

P["M-20"] = {
    "title": "Safe-mode path depends on a device assumed failed (gyro)",
    "c": {"mutant": r'''static double gyro_rate = 0.1, sun_angle = 0.2, wheel_cmd;
static void damp_rates(void) { wheel_cmd = -0.1 * gyro_rate; }
static void enter_survival(void) { damp_rates(); }
int main(void) { enter_survival(); volatile double w = wheel_cmd + sun_angle; (void)w; return 0; }
''', "fixed": r'''static double gyro_rate = 0.1, sun_angle = 0.2, wheel_cmd;
static void point_to_sun(void) { wheel_cmd = -0.05 * sun_angle; }
static void enter_survival(void) { point_to_sun(); }
int main(void) { enter_survival(); volatile double w = wheel_cmd + gyro_rate; (void)w; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Small is Long_Float range -1.0 .. 1.0;\n"
                          "   Gyro_Rate : Small := 0.1;\n   Sun_Angle : Small := 0.2;\n   Wheel_Cmd : Small := 0.0;\n"
                          "   procedure Damp_Rates with Global => (Input => Gyro_Rate, Output => Wheel_Cmd);\n"
                          "   procedure Enter_Survival with Global => (Input => Sun_Angle, Output => Wheel_Cmd);\n",
                          "   procedure Damp_Rates is\n   begin\n      Wheel_Cmd := -0.1 * Gyro_Rate;\n   end Damp_Rates;\n"
                          "   procedure Enter_Survival is\n   begin\n      Damp_Rates;\n   end Enter_Survival;\n",
                          "", "   Enter_Survival;\n   if Wheel_Cmd = 7.0 + Long_Float (K) then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Small is Long_Float range -1.0 .. 1.0;\n"
                         "   Gyro_Rate : Small := 0.1;\n   Sun_Angle : Small := 0.2;\n   Wheel_Cmd : Small := 0.0;\n"
                         "   procedure Point_To_Sun with Global => (Input => Sun_Angle, Output => Wheel_Cmd);\n"
                         "   procedure Enter_Survival with Global => (Input => Sun_Angle, Output => Wheel_Cmd);\n",
                         "   procedure Point_To_Sun is\n   begin\n      Wheel_Cmd := -0.05 * Sun_Angle;\n   end Point_To_Sun;\n"
                         "   procedure Enter_Survival is\n   begin\n      Point_To_Sun;\n   end Enter_Survival;\n",
                         "", "   Enter_Survival;\n   if Wheel_Cmd = 7.0 + Long_Float (K) then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''struct Gyro { rate: f64 } struct Sun { angle: f64 } struct Wheels { cmd: f64 }
fn damp_rates(g: &Gyro, w: &mut Wheels) { w.cmd = -0.1 * g.rate; }
fn enter_survival(_sun: &Sun, w: &mut Wheels) { damp_rates(gyro, w); }
fn main() { let s = Sun { angle: 0.2 }; let mut w = Wheels { cmd: 0.0 }; let g = Gyro { rate: 0.1 }; enter_survival(&s, &mut w); println!("{} {}", w.cmd, g.rate + s.angle); }
''', "fixed": r'''struct Gyro { rate: f64 } struct Sun { angle: f64 } struct Wheels { cmd: f64 }
fn point_to_sun(s: &Sun, w: &mut Wheels) { w.cmd = -0.05 * s.angle; }
fn enter_survival(sun: &Sun, w: &mut Wheels) { point_to_sun(sun, w); }
fn main() { let s = Sun { angle: 0.2 }; let mut w = Wheels { cmd: 0.0 }; let g = Gyro { rate: 0.1 }; enter_survival(&s, &mut w); println!("{} {}", w.cmd, g.rate + s.angle); }
'''},
    "declares": {"ada": True, "rust": True},
    "note": "A SPARK Global contract and Rust capability passing limit the devices the safe path can reach (◆); "
            "there is no notion of a fault hypothesis: the engineer carries into the contract which device counts as failed.",
}

P["M-21"] = {
    "title": "Recursion without a declared bound (stack depth, Toyota class)",
    "c": {"mutant": r'''static int depth(int n) { if (n == 0) { return 0; } return depth(n - 1); }
int main(void) { volatile int n = 100; volatile int d = depth(n); (void)d; return 0; }
''', "fixed": r'''static int depth(int n) { int d = 0; for (int i = 0; i < 100; i++) { if (i < n && d < 100) { d++; } } return d; }
int main(void) { volatile int n = 100; volatile int d = depth(n); (void)d; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Depth_T is Natural range 0 .. 100;\n   function Depth (N : Depth_T) return Depth_T;\n",
                          "   function Depth (N : Depth_T) return Depth_T is (if N = 0 then 0 else Depth (N - 1));\n",
                          "", "   if Depth (100 - K) /= 0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Depth_T is Natural range 0 .. 100;\n   function Depth (N : Depth_T) return Depth_T;\n",
                         "   function Depth (N : Depth_T) return Depth_T is\n      D : Depth_T := 0;\n   begin\n"
                         "      for I in 0 .. 99 loop\n         if I < N and then D < 100 then\n            D := D + 1;\n         end if;\n"
                         "      end loop;\n      return D;\n   end Depth;\n",
                         "", "   if Depth (100 - K) /= 100 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn depth(n: u32) -> u32 { if n == 0 { 0 } else { depth(n - 1) } }
fn main() { println!("{}", depth(std::hint::black_box(100))); }
''', "fixed": r'''fn depth(n: u32) -> u32 { let mut d = 0; for i in 0..100 { if i < n && d < 100 { d += 1; } } d }
fn main() { println!("{}", depth(std::hint::black_box(100))); }
'''},
    "declares": {},
}

P["M-22"] = {
    "title": "Uninitialised read",
    "c": {"mutant": r'''static int f(void) { int x; return x; }
int main(void) { volatile int r = f(); (void)r; return 0; }
''', "fixed": r'''static int f(void) { int x = 0; return x; }
int main(void) { volatile int r = f(); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   subtype T is Integer range 0 .. 10;\n   function F return T;\n",
                          "   function F return T is\n      X : T;\n   begin\n      return X;\n   end F;\n",
                          "", "   if F = 11 - K then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype T is Integer range 0 .. 10;\n   function F return T;\n",
                         "   function F return T is\n      X : T := 0;\n   begin\n      return X;\n   end F;\n",
                         "", "   if F = 11 - K then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn f() -> i32 { let x: i32; x }
fn main() { println!("{}", f()); }
''', "fixed": r'''fn f() -> i32 { let x: i32 = 0; x }
fn main() { println!("{}", f()); }
'''},
    "declares": {},
}

P["M-23"] = {
    "title": "Division by a computed zero denominator",
    "c": {"mutant": r'''static int rate(int delta, int dt_ms) { return delta / dt_ms; }
int main(void) { volatile int d = 500, t = 0; volatile int r = rate(d, t); (void)r; return 0; }
''', "fixed": r'''static int rate(int delta, int dt_ms) { if (dt_ms == 0) { return 0; } return delta / dt_ms; }
int main(void) { volatile int d = 500, t = 0; volatile int r = rate(d, t); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Delta_T is Natural range 0 .. 1000;\n   subtype Dt_T is Natural range 0 .. 100;\n"
                          "   function Rate (D : Delta_T; Dt : Dt_T) return Delta_T;\n",
                          "   function Rate (D : Delta_T; Dt : Dt_T) return Delta_T is (D / Dt);\n",
                          "", "   if Rate (500, K) = 7 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Delta_T is Natural range 0 .. 1000;\n   subtype Dt_T is Natural range 0 .. 100;\n"
                         "   function Rate (D : Delta_T; Dt : Dt_T) return Delta_T;\n",
                         "   function Rate (D : Delta_T; Dt : Dt_T) return Delta_T is (if Dt = 0 then 0 else D / Dt);\n",
                         "", "   if Rate (500, K) = 7 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn rate(delta: u32, dt_ms: u32) -> u32 { delta / dt_ms }
fn main() { println!("{}", rate(500, std::hint::black_box(0))); }
''', "fixed": r'''fn rate(delta: u32, dt_ms: u32) -> u32 { if dt_ms == 0 { 0 } else { delta / dt_ms } }
fn main() { println!("{}", rate(500, std::hint::black_box(0))); }
'''},
    "declares": {},
}

P["M-24"] = {
    "title": "Implicit rounding in fixed point (rounding direction not stated)",
    "c": {"mutant": r'''#include <stdint.h>
static int16_t control_step(int16_t err, int16_t gain) { int32_t raw = (int32_t)err * (int32_t)gain; return (int16_t)(raw >> 18); }
int main(void) { volatile int16_t e = -2048, g = 16384; volatile int16_t r = control_step(e, g); (void)r; return 0; }
''', "fixed": r'''#include <stdint.h>
static int32_t round_nearest_even(int32_t x, int32_t p) {
    int32_t q = x / p; int32_t r = x % p;
    if (r < 0) { q -= 1; r += p; }
    if (2 * r > p || (2 * r == p && (q % 2) != 0)) { q += 1; }
    return q; }
static int16_t control_step(int16_t err, int16_t gain) { int32_t raw = (int32_t)err * (int32_t)gain; return (int16_t)round_nearest_even(raw, 262144); }
int main(void) { volatile int16_t e = -2048, g = 16384; volatile int16_t r = control_step(e, g); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   type Err_T is delta 2.0 ** (-12) range -8.0 .. 8.0 - 2.0 ** (-12);\n"
                          "   type Gain_T is delta 2.0 ** (-14) range -2.0 .. 2.0 - 2.0 ** (-14);\n"
                          "   type Out_T is delta 2.0 ** (-8) range -128.0 .. 128.0 - 2.0 ** (-8);\n"
                          "   function Control_Step (E : Err_T; G : Gain_T) return Out_T;\n",
                          "   function Control_Step (E : Err_T; G : Gain_T) return Out_T is (Out_T (E * G));\n",
                          "   R : Out_T;\n", "   R := Control_Step (-0.5 + Err_T (K), 1.0);\n   if R = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   type Err_T is delta 2.0 ** (-12) range -8.0 .. 8.0 - 2.0 ** (-12);\n"
                         "   type Gain_T is delta 2.0 ** (-14) range -2.0 .. 2.0 - 2.0 ** (-14);\n"
                         "   type Out_T is delta 2.0 ** (-8) range -128.0 .. 128.0 - 2.0 ** (-8);\n"
                         "   function Control_Step (E : Err_T; G : Gain_T) return Out_T;\n",
                         "   function Control_Step (E : Err_T; G : Gain_T) return Out_T is (Out_T'Round (E * G));\n",
                         "   R : Out_T;\n", "   R := Control_Step (-0.5 + Err_T (K), 1.0);\n   if R = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn control_step(err: i16, gain: i16) -> i16 { let raw = i32::from(err) * i32::from(gain); (raw >> 18) as i16 }
fn main() { println!("{}", control_step(std::hint::black_box(-2048), 16384)); }
''', "fixed": r'''fn round_nearest_even(x: i32, p: i32) -> i32 { let q = x.div_euclid(p); let r = x.rem_euclid(p); if 2 * r > p || (2 * r == p && q % 2 != 0) { q + 1 } else { q } }
fn control_step(err: i16, gain: i16) -> i16 { let raw = i32::from(err) * i32::from(gain); round_nearest_even(raw, 262_144) as i16 }
fn main() { println!("{}", control_step(std::hint::black_box(-2048), 16384)); }
'''},
    "declares": {},
}

_MID_C = r'''static int mid(int a, int b, int c) { if (a <= b) { if (b <= c) return b; return a <= c ? c : a; } if (a <= c) return a; return b <= c ? c : b; }
'''
P["M-25"] = {
    "title": "Three channels + unsigned link, one Byzantine channel (Driscoll 2003)",
    "c": {"mutant": _MID_C + r'''int main(void) { volatile int a = 1, b = 2, c = 3; volatile int m = mid(a, b, c); (void)m; return 0; }
''', "fixed": _MID_C + r'''int main(void) { volatile int a = 1, b = 2, c = 3; volatile int m = mid(a, b, c); (void)m; return 0; }
'''},
    "ada": {"mutant": ada("   subtype V is Integer range -1000 .. 1000;\n   function Mid (A, B, C : V) return V;\n",
                          "   function Mid (A, B, C : V) return V is\n     (if A <= B then (if B <= C then B elsif A <= C then C else A)\n"
                          "      elsif A <= C then A elsif B <= C then C else B);\n",
                          "", "   if Mid (1 + K, 2, 3) /= 2 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype V is Integer range -1000 .. 1000;\n   function Mid (A, B, C : V) return V;\n",
                         "   function Mid (A, B, C : V) return V is\n     (if A <= B then (if B <= C then B elsif A <= C then C else A)\n"
                         "      elsif A <= C then A elsif B <= C then C else B);\n",
                         "", "   if Mid (1 + K, 2, 3) /= 2 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn mid(a: i32, b: i32, c: i32) -> i32 { if a <= b { if b <= c { b } else if a <= c { c } else { a } } else if a <= c { a } else if b <= c { c } else { b } }
fn main() { println!("{}", mid(std::hint::black_box(1), 2, 3)); }
''', "fixed": r'''fn mid(a: i32, b: i32, c: i32) -> i32 { if a <= b { if b <= c { b } else if a <= c { c } else { a } } else if a <= c { a } else if b <= c { c } else { b } }
fn main() { println!("{}", mid(std::hint::black_box(1), 2, 3)); }
'''},
    "declares": {},
    "note": "The error is in the architecture (channel count / exchange protocol); no competing language has an architecture declaration, so the mutant and "
            "fixed programs are the same code.",
}

_QF_C = r'''static double memo; static int hold;
static double aoa_filter(double a1, double a2, double a3) {
    double avg = (a1 + a2) / 2.0;
    if (hold > 0) {
        hold--;
        if (hold > 0) { return memo; }
%EXPIRY%    }
    if (a1 - a3 > 2.0 || a3 - a1 > 2.0 || a2 - a3 > 2.0 || a3 - a2 > 2.0) { hold = 3; return memo; }
    memo = avg; return avg;
}
int main(void) {
    volatile double a1[7] = {0, 2.25, 0, 0, 30.0, 0, 0}; double out = 0.0;
    for (int k = 0; k < 7; k++) { out = aoa_filter(a1[k], 0.0, 0.0); }
    return out > 4.0 ? 0 : 0; }
'''
_QF_ADA_SPEC = ("   subtype Deg is Long_Float range -30.0 .. 30.0;\n   Memo : Deg := 0.0;\n   Hold : Natural range 0 .. 3 := 0;\n"
                "   procedure AOA_Filter (A1, A2, A3 : Deg; Out_V : out Deg);\n")
_QF_ADA_BODY = ("   procedure AOA_Filter (A1, A2, A3 : Deg; Out_V : out Deg) is\n      Avg : constant Deg := (A1 + A2) / 2.0;\n   begin\n"
                "      if Hold > 0 then\n         Hold := Hold - 1;\n         if Hold > 0 then\n            Out_V := Memo;\n            return;\n         end if;\n"
                "%EXPIRY%      end if;\n"
                "      if abs (A1 - A3) > 2.0 or else abs (A2 - A3) > 2.0 then\n         Hold := 3;\n         Out_V := Memo;\n         return;\n      end if;\n"
                "      Memo := Avg;\n      Out_V := Avg;\n   end AOA_Filter;\n")
_QF_RS = r'''struct F { memo: f64, hold: u8 }
impl F { fn aoa_filter(&mut self, a1: f64, a2: f64, a3: f64) -> f64 {
    let avg = (a1 + a2) / 2.0;
    if self.hold > 0 { self.hold -= 1; if self.hold > 0 { return self.memo; } %EXPIRY% }
    if (a1 - a3).abs() > 2.0 || (a2 - a3).abs() > 2.0 { self.hold = 3; return self.memo; }
    self.memo = avg; avg } }
fn main() { let mut f = F { memo: 0.0, hold: 0 }; let mut out = 0.0; for a in [0.0, 2.25, 0.0, 0.0, 30.0, 0.0, 0.0] { out = f.aoa_filter(std::hint::black_box(a), 0.0, 0.0); } println!("{out}"); }
'''
P["M-26"] = {
    "title": "Second spike passes the filter as the hold expires (Qantas 72)",
    "c": {"mutant": _QF_C.replace("%EXPIRY%", "        memo = avg; return avg;\n"),
          "fixed": _QF_C.replace("%EXPIRY%", "")},
    "ada": {"mutant": ada(_QF_ADA_SPEC, _QF_ADA_BODY.replace("%EXPIRY%", "         Memo := Avg;\n         Out_V := Avg;\n         return;\n"),
                          "   type Seq is array (1 .. 7) of Deg;\n   S : constant Seq := (0.0, 2.25, 0.0, 0.0, 30.0, 0.0, 0.0);\n   O : Deg := 0.0;\n",
                          "   for I in S'Range loop\n      AOA_Filter (S (I) * Long_Float (1 - K), 0.0, 0.0, O);\n   end loop;\n   if O > 4.0 then Set_Exit_Status (0); end if;\n"),
            "fixed": ada(_QF_ADA_SPEC, _QF_ADA_BODY.replace("%EXPIRY%", ""),
                         "   type Seq is array (1 .. 7) of Deg;\n   S : constant Seq := (0.0, 2.25, 0.0, 0.0, 30.0, 0.0, 0.0);\n   O : Deg := 0.0;\n",
                         "   for I in S'Range loop\n      AOA_Filter (S (I) * Long_Float (1 - K), 0.0, 0.0, O);\n   end loop;\n   if O > 4.0 then Set_Exit_Status (0); end if;\n")},
    "rust": {"mutant": _QF_RS.replace("%EXPIRY%", "self.memo = avg; return avg;"), "fixed": _QF_RS.replace("%EXPIRY%", "")},
    "declares": {},
    "note": "The error is a corner case that appears only on arbitrary input sequences; no competing tool checks an output bound "
            "over time. The mutant lets the spike through (output 15 deg) but no run-time check fires.",
}
