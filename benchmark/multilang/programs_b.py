from programs_a import ada

P = {}

_C_READING = r'''typedef enum { VALID, SATURATED, STALE, INVALID } status_t;
typedef struct { status_t status; double value; } reading_t;
static reading_t read_rate(void) { volatile int s = 1; reading_t r; r.status = (status_t)s; r.value = 9.9; return r; }
'''
_ADA_READING = ("   subtype Rate is Long_Float range -100.0 .. 100.0;\n"
                "   subtype Gain_T is Long_Float range 0.0 .. 1.0;\n"
                "   type Status_Kind is (Valid, Saturated, Stale, Invalid);\n"
                "   type Reading (Status : Status_Kind := Invalid) is record\n"
                "      case Status is\n"
                "         when Valid => Value : Rate;\n"
                "         when Saturated => Sat_Value : Rate;\n"
                "         when Stale => Old_Value : Rate;\n"
                "         when Invalid => null;\n"
                "      end case;\n   end record;\n")
_ADA_READING_MAIN = ("   W : constant Reading := (if K = 0 then (Status => Saturated, Sat_Value => 9.9)\n"
                     "                            else (Status => Valid, Value => 1.0));\n   E : Long_Float;\n")
_RUST_READING = "#[derive(Clone, Copy)] enum Reading { Valid(f64), Saturated(f64), Stale(f64), Invalid }\n"

P["M-10"] = {
    "title": "Suspect (saturated) measurement used directly (Schiaparelli)",
    "c": {"mutant": _C_READING + r'''static double estimate(double gain) { reading_t w = read_rate(); return w.value * gain; }
int main(void) { volatile double g = 0.5; volatile double e = estimate(g); (void)e; return 0; }
''', "fixed": _C_READING + r'''static double estimate(double gain) { reading_t w = read_rate(); if (w.status == VALID) { return w.value * gain; } return 0.0; }
int main(void) { volatile double g = 0.5; volatile double e = estimate(g); (void)e; return 0; }
'''},
    "ada": {"mutant": ada(_ADA_READING + "   function Estimate (W : Reading; Gain : Gain_T) return Long_Float;\n",
                          "   function Estimate (W : Reading; Gain : Gain_T) return Long_Float is (W.Value * Gain);\n",
                          _ADA_READING_MAIN, "   E := Estimate (W, 0.5);\n   if E = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada(_ADA_READING + "   function Estimate (W : Reading; Gain : Gain_T) return Long_Float;\n",
                         "   function Estimate (W : Reading; Gain : Gain_T) return Long_Float is\n"
                         "     (case W.Status is when Valid => W.Value * Gain, when others => 0.0);\n",
                         _ADA_READING_MAIN, "   E := Estimate (W, 0.5);\n   if E = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": _RUST_READING + r'''fn read_rate() -> Reading { if std::hint::black_box(true) { Reading::Saturated(9.9) } else { Reading::Invalid } }
fn estimate(gain: f64) -> f64 { let w = read_rate(); w * gain }
fn main() { println!("{}", estimate(0.5)); }
''', "fixed": _RUST_READING + r'''fn read_rate() -> Reading { if std::hint::black_box(true) { Reading::Saturated(9.9) } else { Reading::Invalid } }
fn estimate(gain: f64) -> f64 { match read_rate() { Reading::Valid(w) => w * gain, Reading::Saturated(_) | Reading::Stale(_) | Reading::Invalid => 0.0 } }
fn main() { println!("{}", estimate(0.5)); }
'''},
    "declares": {"ada": True, "rust": True},
}

P["M-11"] = {
    "title": "Last good value persists without an age limit (Schiaparelli)",
    "c": {"mutant": _C_READING + r'''static double last_rate;
static double estimate(void) { reading_t r = read_rate(); if (r.status == VALID) { last_rate = r.value; return r.value; } return last_rate; }
int main(void) { volatile double e = estimate(); (void)e; return 0; }
''', "fixed": _C_READING + r'''static double last_rate, last_t;
static double estimate(double now) {
    reading_t r = read_rate();
    if (r.status == VALID) { last_rate = r.value; last_t = now; return r.value; }
    if (now - last_t > 0.05) { return 0.0; }
    return last_rate;
}
int main(void) { volatile double now = 1.0; volatile double e = estimate(now); (void)e; return 0; }
'''},
    "ada": {"mutant": ada(_ADA_READING + "   Last_Rate : Rate := 0.0;\n   procedure Estimate (W : Reading; E : out Long_Float);\n",
                          "   procedure Estimate (W : Reading; E : out Long_Float) is\n   begin\n"
                          "      if W.Status = Valid then\n         Last_Rate := W.Value;\n         E := W.Value;\n"
                          "      else\n         E := Last_Rate;\n      end if;\n   end Estimate;\n",
                          _ADA_READING_MAIN, "   Estimate (W, E);\n   if E = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada(_ADA_READING + "   Last_Rate : Rate := 0.0;\n   Last_T : Long_Float := 0.0;\n"
                         "   procedure Estimate (W : Reading; Now : Long_Float; E : out Long_Float)\n"
                         "     with Pre => Now in 0.0 .. 1.0E6 and Last_T in 0.0 .. 1.0E6;\n",
                         "   procedure Estimate (W : Reading; Now : Long_Float; E : out Long_Float) is\n   begin\n"
                         "      if W.Status = Valid then\n         Last_Rate := W.Value;\n         Last_T := Now;\n         E := W.Value;\n"
                         "      elsif Now - Last_T > 0.05 then\n         E := 0.0;\n"
                         "      else\n         E := Last_Rate;\n      end if;\n   end Estimate;\n",
                         _ADA_READING_MAIN, "   Estimate (W, 1.0, E);\n   if E = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": _RUST_READING + r'''struct Est { last: f64 }
impl Est { fn estimate(&mut self, r: Reading) -> f64 { match r { Reading::Valid(w) => { self.last = w; w } _ => self.last } } }
fn main() { let mut e = Est { last: 0.0 }; println!("{}", e.estimate(std::hint::black_box(Reading::Invalid))); }
''', "fixed": _RUST_READING + r'''struct Est { last: f64, last_t: f64 }
impl Est { fn estimate(&mut self, r: Reading, now: f64) -> f64 { match r { Reading::Valid(w) => { self.last = w; self.last_t = now; w } _ if now - self.last_t > 0.05 => 0.0, _ => self.last } } }
fn main() { let mut e = Est { last: 0.0, last_t: 0.0 }; println!("{}", e.estimate(std::hint::black_box(Reading::Invalid), 1.0)); }
'''},
    "declares": {},
}

P["M-12"] = {
    "title": "Transient signal latched without debounce (Mars Polar Lander)",
    "c": {"mutant": r'''#include <stdbool.h>
static bool touchdown;
static void update(bool contact) { if (contact) { touchdown = true; } }
int main(void) { volatile bool c = true; update(c); return touchdown ? 0 : 1; }
''', "fixed": r'''#include <stdbool.h>
static bool touchdown; static int count;
static void update(bool contact) { if (contact) { if (count < 3) { count++; } if (count >= 3) { touchdown = true; } } else { count = 0; } }
int main(void) { volatile bool c = true; update(c); return touchdown ? 1 : 0; }
'''},
    "ada": {"mutant": ada("   Touchdown : Boolean := False;\n   procedure Update (Contact : Boolean);\n",
                          "   procedure Update (Contact : Boolean) is\n   begin\n      if Contact then\n         Touchdown := True;\n      end if;\n   end Update;\n",
                          "", "   Update (K = 0);\n   if not Touchdown then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   Touchdown : Boolean := False;\n   Count : Natural range 0 .. 3 := 0;\n   procedure Update (Contact : Boolean);\n",
                         "   procedure Update (Contact : Boolean) is\n   begin\n      if Contact then\n"
                         "         if Count < 3 then\n            Count := Count + 1;\n         end if;\n"
                         "         if Count >= 3 then\n            Touchdown := True;\n         end if;\n"
                         "      else\n         Count := 0;\n      end if;\n   end Update;\n",
                         "", "   Update (K = 0);\n   if Touchdown then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''struct Lander { touchdown: bool }
impl Lander { fn update(&mut self, contact: bool) { if contact { self.touchdown = true; } } }
fn main() { let mut l = Lander { touchdown: false }; l.update(std::hint::black_box(true)); println!("{}", l.touchdown); }
''', "fixed": r'''struct Lander { touchdown: bool, count: u8 }
impl Lander { fn update(&mut self, contact: bool) { if contact { if self.count < 3 { self.count += 1; } if self.count >= 3 { self.touchdown = true; } } else { self.count = 0; } } }
fn main() { let mut l = Lander { touchdown: false, count: 0 }; l.update(std::hint::black_box(true)); println!("{}", l.touchdown); }
'''},
    "declares": {},
}

_C_QUEUE = r'''#include <stdint.h>
typedef struct { uint8_t data[8]; unsigned len; } queue_t;
__attribute__((warn_unused_result)) static int push(queue_t *q, uint8_t v) { if (q->len >= 8u) { return 1; } q->data[q->len] = v; q->len++; return 0; }
'''
_ADA_QUEUE = ("   type Push_Result is (Ok, Full);\n   type Store is array (1 .. 8) of Unsigned_8;\n"
              "   type Queue is record\n      Data : Store := (others => 0);\n      Len : Natural range 0 .. 8 := 0;\n   end record;\n"
              "   procedure Push (Q : in out Queue; V : Unsigned_8; R : out Push_Result);\n"
              "   function Enqueue return Natural;\n")
_ADA_QUEUE_BODY = ("   procedure Push (Q : in out Queue; V : Unsigned_8; R : out Push_Result) is\n   begin\n"
                   "      if Q.Len = 8 then\n         R := Full;\n      else\n         Q.Len := Q.Len + 1;\n"
                   "         Q.Data (Q.Len) := V;\n         R := Ok;\n      end if;\n   end Push;\n")
_RUST_QUEUE = r'''#[derive(Debug)] struct Full;
struct Queue { data: [u8; 8], len: usize }
impl Queue { fn push(&mut self, v: u8) -> Result<(), Full> { if self.len >= 8 { return Err(Full); } self.data[self.len] = v; self.len += 1; Ok(()) } }
'''
P["M-13"] = {
    "title": "'Full' result of a bounded queue ignored (Spirit, sol 18)",
    "c": {"mutant": _C_QUEUE + r'''static int enqueue(void) { queue_t q = {{0}, 0u}; push(&q, 42u); return 0; }
int main(void) { return enqueue(); }
''', "fixed": _C_QUEUE + r'''static int enqueue(void) { queue_t q = {{0}, 0u}; if (push(&q, 42u) != 0) { return 1; } return 0; }
int main(void) { return enqueue(); }
'''},
    "ada": {"mutant": ada(_ADA_QUEUE, _ADA_QUEUE_BODY + "   function Enqueue return Natural is\n      Q : Queue;\n      R : Push_Result;\n   begin\n"
                          "      Push (Q, 42, R);\n      return 0;\n   end Enqueue;\n",
                          "", "   if Enqueue /= K then Set_Exit_Status (2); end if;\n"),
            "fixed": ada(_ADA_QUEUE, _ADA_QUEUE_BODY + "   function Enqueue return Natural is\n      Q : Queue;\n      R : Push_Result;\n   begin\n"
                         "      Push (Q, 42, R);\n      if R = Full then\n         return 1;\n      end if;\n      return 0;\n   end Enqueue;\n",
                         "", "   if Enqueue /= K then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": _RUST_QUEUE + r'''fn enqueue() -> u8 { let mut q = Queue { data: [0; 8], len: 0 }; q.push(42); q.data[0] }
fn main() { println!("{}", enqueue()); }
''', "fixed": _RUST_QUEUE + r'''fn enqueue() -> u8 { let mut q = Queue { data: [0; 8], len: 0 }; if q.push(42).is_err() { return 0; } q.data[0] }
fn main() { println!("{}", enqueue()); }
'''},
    "declares": {"c": True},
    "note": "In C, gcc warns only with the warn_unused_result attribute (◆); MISRA Rule 17.7 makes this mandatory.",
}

P["M-14"] = {
    "title": "Backup channel shares the primary's code (Ariane 501 SRI1/SRI2)",
    "c": {"mutant": r'''#include <stdint.h>
static int16_t bias_filter(int16_t raw) { return (int16_t)(raw - 2048); }
static int16_t rate_a(int16_t g) { return bias_filter(g); }
static int16_t rate_b(int16_t g) { return bias_filter(g); }
int main(void) { volatile int16_t g = 100; volatile int16_t r = (int16_t)(rate_a(g) + rate_b(g)); (void)r; return 0; }
''', "fixed": r'''#include <stdint.h>
static int16_t offset_a(int16_t raw) { return (int16_t)(raw - 2048); }
static int16_t offset_b(int16_t raw) { if (raw >= 2048) { return (int16_t)(raw - 2048); } return (int16_t)(-(2048 - raw)); }
static int16_t rate_a(int16_t g) { return offset_a(g); }
static int16_t rate_b(int16_t g) { return offset_b(g); }
int main(void) { volatile int16_t g = 100; volatile int16_t r = (int16_t)(rate_a(g) + rate_b(g)); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Raw_T is Integer range 0 .. 4095;\n   subtype Rate_T is Integer range -2048 .. 2047;\n"
                          "   function Rate_A (G : Raw_T) return Rate_T;\n   function Rate_B (G : Raw_T) return Rate_T;\n",
                          "   function Bias_Filter (Raw : Raw_T) return Rate_T is (Raw - 2048);\n"
                          "   function Rate_A (G : Raw_T) return Rate_T is (Bias_Filter (G));\n"
                          "   function Rate_B (G : Raw_T) return Rate_T is (Bias_Filter (G));\n",
                          "", "   if Rate_A (100 + K) /= Rate_B (100 + K) then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Raw_T is Integer range 0 .. 4095;\n   subtype Rate_T is Integer range -2048 .. 2047;\n"
                         "   function Rate_A (G : Raw_T) return Rate_T;\n   function Rate_B (G : Raw_T) return Rate_T;\n",
                         "   function Offset_A (Raw : Raw_T) return Rate_T is (Raw - 2048);\n"
                         "   function Offset_B (Raw : Raw_T) return Rate_T is (if Raw >= 2048 then Raw - 2048 else -(2048 - Raw));\n"
                         "   function Rate_A (G : Raw_T) return Rate_T is (Offset_A (G));\n"
                         "   function Rate_B (G : Raw_T) return Rate_T is (Offset_B (G));\n",
                         "", "   if Rate_A (100 + K) /= Rate_B (100 + K) then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn bias_filter(raw: i16) -> i16 { raw - 2048 }
fn rate_a(g: i16) -> i16 { bias_filter(g) }
fn rate_b(g: i16) -> i16 { bias_filter(g) }
fn main() { let g = std::hint::black_box(100); println!("{} {}", rate_a(g), rate_b(g)); }
''', "fixed": r'''fn offset_a(raw: i16) -> i16 { raw - 2048 }
fn offset_b(raw: i16) -> i16 { if raw >= 2048 { raw - 2048 } else { -(2048 - raw) } }
fn rate_a(g: i16) -> i16 { offset_a(g) }
fn rate_b(g: i16) -> i16 { offset_b(g) }
fn main() { let g = std::hint::black_box(100); println!("{} {}", rate_a(g), rate_b(g)); }
'''},
    "declares": {},
}

P["M-15"] = {
    "title": "Halt on fault during the flight phase (Ariane 501 policy)",
    "c": {"mutant": r'''#include <stdlib.h>
static int flight_phase = 1;
static void on_overflow(void) { abort(); }
int main(void) { volatile int ph = 0; flight_phase = ph; if (flight_phase) { on_overflow(); } return 0; }
''', "fixed": r'''static int flight_phase = 1; static int degraded;
static void on_overflow(void) { degraded = 1; }
int main(void) { volatile int ph = 0; flight_phase = ph; if (flight_phase) { on_overflow(); } return degraded; }
'''},
    "ada": {"mutant": ada("   Degraded : Boolean := False;\n   procedure On_Overflow;\n",
                          "   procedure On_Overflow is\n   begin\n      raise Program_Error;\n   end On_Overflow;\n",
                          "", "   if K > 0 then\n      On_Overflow;\n   end if;\n"),
            "fixed": ada("   Degraded : Boolean := False;\n   procedure On_Overflow;\n",
                         "   procedure On_Overflow is\n   begin\n      Degraded := True;\n   end On_Overflow;\n",
                         "", "   if K > 0 then\n      On_Overflow;\n   end if;\n")},
    "rust": {"mutant": r'''fn on_overflow() { panic!("assume hardware failure"); }
fn main() { if std::hint::black_box(false) { on_overflow(); } println!("ok"); }
''', "fixed": r'''fn on_overflow(degraded: &mut bool) { *degraded = true; }
fn main() { let mut d = false; if std::hint::black_box(false) { on_overflow(&mut d); } println!("{d}"); }
'''},
    "declares": {},
    "note": "MISRA Rule 21.8 forbids abort everywhere; SPARK rejects a reachable raise; neither makes the 'in the flight phase' "
            "distinction (a blind ban). Spelf allows halting before lift-off.",
}

P["M-16"] = {
    "title": "Out-of-range configuration constant (Titan IVB / Milstar)",
    "c": {"mutant": r'''static const double ROLL_RATE_GAIN = -1.992476;
int main(void) { volatile double x = 1.0; volatile double y = x * ROLL_RATE_GAIN; (void)y; return 0; }
''', "fixed": r'''static const double ROLL_RATE_GAIN = -0.1992476;
int main(void) { volatile double x = 1.0; volatile double y = x * ROLL_RATE_GAIN; (void)y; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Gain_T is Long_Float range -1.0 .. 0.0;\n   Roll_Rate_Gain : constant Gain_T := -1.992476;\n"
                          "   function Filter (X : Long_Float) return Long_Float with Pre => X in -1.0 .. 1.0;\n",
                          "   function Filter (X : Long_Float) return Long_Float is (X * Roll_Rate_Gain);\n",
                          "   Y : Long_Float;\n", "   Y := Filter (Long_Float (K));\n   if Y = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Gain_T is Long_Float range -1.0 .. 0.0;\n   Roll_Rate_Gain : constant Gain_T := -0.1992476;\n"
                         "   function Filter (X : Long_Float) return Long_Float with Pre => X in -1.0 .. 1.0;\n",
                         "   function Filter (X : Long_Float) return Long_Float is (X * Roll_Rate_Gain);\n",
                         "   Y : Long_Float;\n", "   Y := Filter (Long_Float (K));\n   if Y = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''const ROLL_RATE_GAIN: f64 = -1.992_476;
const _: () = assert!(ROLL_RATE_GAIN >= -1.0 && ROLL_RATE_GAIN <= 0.0);
fn main() { println!("{}", std::hint::black_box(1.0) * ROLL_RATE_GAIN); }
''', "fixed": r'''const ROLL_RATE_GAIN: f64 = -0.199_247_6;
const _: () = assert!(ROLL_RATE_GAIN >= -1.0 && ROLL_RATE_GAIN <= 0.0);
fn main() { println!("{}", std::hint::black_box(1.0) * ROLL_RATE_GAIN); }
'''},
    "declares": {"ada": True, "rust": True},
}

P["M-17"] = {
    "title": "Error quaternion produced without canonicalisation (turning the long way)",
    "c": {"mutant": r'''#include <assert.h>
typedef struct { double w, x, y, z; } quat_t;
static quat_t error_quat(double w, double x, double y, double z) { quat_t q = { w, x, y, z }; assert(q.w >= 0.0); return q; }
int main(void) { volatile double w = -0.6; quat_t q = error_quat(w, 0.8, 0.0, 0.0); return (q.w + q.x + q.y + q.z) > 9.0 ? 1 : 0; }
''', "fixed": r'''#include <assert.h>
typedef struct { double w, x, y, z; } quat_t;
static quat_t error_quat(double w, double x, double y, double z) {
    quat_t q = { w, x, y, z };
    if (q.w < 0.0) { q.w = -q.w; q.x = -q.x; q.y = -q.y; q.z = -q.z; }
    assert(q.w >= 0.0); return q; }
int main(void) { volatile double w = -0.6; quat_t q = error_quat(w, 0.8, 0.0, 0.0); return (q.w + q.x + q.y + q.z) > 9.0 ? 1 : 0; }
'''},
    "ada": {"mutant": ada("   subtype Unit_Comp is Long_Float range -1.0 .. 1.0;\n"
                          "   type Quat is record\n      W, X, Y, Z : Unit_Comp;\n   end record;\n"
                          "   subtype Canonical_Quat is Quat with Dynamic_Predicate => Canonical_Quat.W >= 0.0;\n"
                          "   function Error_Quat (W, X, Y, Z : Unit_Comp) return Canonical_Quat;\n",
                          "   function Error_Quat (W, X, Y, Z : Unit_Comp) return Canonical_Quat is ((W, X, Y, Z));\n",
                          "   Q : Quat;\n", "   Q := Error_Quat (-0.6 + Long_Float (K), 0.8, 0.0, 0.0);\n   if Q.W = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Unit_Comp is Long_Float range -1.0 .. 1.0;\n"
                         "   type Quat is record\n      W, X, Y, Z : Unit_Comp;\n   end record;\n"
                         "   subtype Canonical_Quat is Quat with Dynamic_Predicate => Canonical_Quat.W >= 0.0;\n"
                         "   function Error_Quat (W, X, Y, Z : Unit_Comp) return Canonical_Quat;\n",
                         "   function Error_Quat (W, X, Y, Z : Unit_Comp) return Canonical_Quat is\n"
                         "     (if W < 0.0 then (-W, -X, -Y, -Z) else (W, X, Y, Z));\n",
                         "   Q : Quat;\n", "   Q := Error_Quat (-0.6 + Long_Float (K), 0.8, 0.0, 0.0);\n   if Q.W = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''#[derive(Clone, Copy, Debug)] struct Quat { w: f64, x: f64, y: f64, z: f64 }
fn error_quat(w: f64, x: f64, y: f64, z: f64) -> Quat { let q = Quat { w, x, y, z }; debug_assert!(q.w >= 0.0); q }
fn main() { println!("{:?}", error_quat(std::hint::black_box(-0.6), 0.8, 0.0, 0.0)); }
''', "fixed": r'''#[derive(Clone, Copy, Debug)] struct Quat { w: f64, x: f64, y: f64, z: f64 }
fn error_quat(w: f64, x: f64, y: f64, z: f64) -> Quat {
    let q = if w < 0.0 { Quat { w: -w, x: -x, y: -y, z: -z } } else { Quat { w, x, y, z } };
    debug_assert!(q.w >= 0.0); q }
fn main() { println!("{:?}", error_quat(std::hint::black_box(-0.6), 0.8, 0.0, 0.0)); }
'''},
    "declares": {"c": True, "ada": True, "rust": True},
}

P["M-18"] = {
    "title": "Saturated command used without knowing it was clamped (integrator windup)",
    "c": {"mutant": r'''static double clamp(double u, double lo, double hi) { return u < lo ? lo : (u > hi ? hi : u); }
static double integ;
static double torque_command(double u) { double c = clamp(u, -1.0, 1.0); integ += u; return c; }
int main(void) { volatile double u = 3.0; volatile double c = torque_command(u); (void)c; return 0; }
''', "fixed": r'''static double clamp(double u, double lo, double hi) { return u < lo ? lo : (u > hi ? hi : u); }
static double integ;
static double torque_command(double u) { double c = clamp(u, -1.0, 1.0); if (c == u) { integ += u; } return c; }
int main(void) { volatile double u = 3.0; volatile double c = torque_command(u); (void)c; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Cmd is Long_Float range -10.0 .. 10.0;\n   Integ : Long_Float range -1.0E6 .. 1.0E6 := 0.0;\n"
                          "   procedure Torque_Command (U : Cmd; C : out Long_Float);\n",
                          "   procedure Torque_Command (U : Cmd; C : out Long_Float) is\n   begin\n"
                          "      C := Long_Float'Max (-1.0, Long_Float'Min (1.0, U));\n"
                          "      if Integ + U in -1.0E6 .. 1.0E6 then\n         Integ := Integ + U;\n      end if;\n   end Torque_Command;\n",
                          "   C : Long_Float;\n", "   Torque_Command (3.0 + Long_Float (K), C);\n   if C = 7.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Cmd is Long_Float range -10.0 .. 10.0;\n   Integ : Long_Float range -1.0E6 .. 1.0E6 := 0.0;\n"
                         "   procedure Torque_Command (U : Cmd; C : out Long_Float);\n",
                         "   procedure Torque_Command (U : Cmd; C : out Long_Float) is\n   begin\n"
                         "      C := Long_Float'Max (-1.0, Long_Float'Min (1.0, U));\n"
                         "      if C = U and then Integ + U in -1.0E6 .. 1.0E6 then\n         Integ := Integ + U;\n      end if;\n   end Torque_Command;\n",
                         "   C : Long_Float;\n", "   Torque_Command (3.0 + Long_Float (K), C);\n   if C = 7.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''struct Ctl { integ: f64 }
impl Ctl { fn torque_command(&mut self, u: f64) -> f64 { let c = u.clamp(-1.0, 1.0); self.integ += u; c } }
fn main() { let mut k = Ctl { integ: 0.0 }; println!("{} {}", k.torque_command(std::hint::black_box(3.0)), k.integ); }
''', "fixed": r'''struct Ctl { integ: f64 }
impl Ctl { fn torque_command(&mut self, u: f64) -> f64 { let c = u.clamp(-1.0, 1.0); if (c - u).abs() < f64::EPSILON { self.integ += u; } c } }
fn main() { let mut k = Ctl { integ: 0.0 }; println!("{} {}", k.torque_command(std::hint::black_box(3.0)), k.integ); }
'''},
    "declares": {},
}
