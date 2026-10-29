def ada(spec: str, body: str, main_decls: str, main_stmts: str, extra: dict | None = None,
        main_withs: str = "", spark_main: bool = False) -> dict:
    files = {
        "lib.ads": "with Interfaces; use Interfaces;\npackage Lib with SPARK_Mode is\n" + spec + "end Lib;\n",
        "lib.adb": "package body Lib with SPARK_Mode is\n" + body + "end Lib;\n",
        "main.adb": ("with Ada.Command_Line; use Ada.Command_Line;\nwith Interfaces; use Interfaces;\n"
                     "with Lib; use Lib;\n" + main_withs +
                     f"procedure Main with SPARK_Mode => {'On' if spark_main else 'Off'} is\n"
                     "   K : constant Natural := Argument_Count;\n"
                     + main_decls + "begin\n" + main_stmts + "end Main;\n"),
    }
    if extra:
        files.update(extra)
    return files

P = {}

P["M-01"] = {
    "title": "Unguarded narrowing f64 → i16 (Ariane 501)",
    "c": {"mutant": r'''#include <stdint.h>
static int16_t horizontal_bias(double bh) { return (int16_t)bh; }
int main(void) { volatile double bh = 64000.0; volatile int16_t r = horizontal_bias(bh); (void)r; return 0; }
''', "fixed": r'''#include <stdint.h>
static int16_t horizontal_bias(double bh) {
    if (bh > 32767.0) { return (int16_t)32767; }
    if (bh < -32768.0) { return (int16_t)-32768; }
    return (int16_t)bh;
}
int main(void) { volatile double bh = 64000.0; volatile int16_t r = horizontal_bias(bh); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   function Horizontal_Bias (BH : Long_Float) return Integer_16;\n",
                          "   function Horizontal_Bias (BH : Long_Float) return Integer_16 is (Integer_16 (BH));\n",
                          "   R : Integer_16;\n",
                          "   R := Horizontal_Bias (64000.0 + Long_Float (K));\n   if R = 0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   function Horizontal_Bias (BH : Long_Float) return Integer_16;\n",
                         "   function Horizontal_Bias (BH : Long_Float) return Integer_16 is\n"
                         "     (if BH > 32767.0 then 32767 elsif BH < -32768.0 then -32768 else Integer_16 (BH));\n",
                         "   R : Integer_16;\n",
                         "   R := Horizontal_Bias (64000.0 + Long_Float (K));\n   if R = 0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn horizontal_bias(bh: f64) -> i16 { bh as i16 }
fn main() { let r = horizontal_bias(std::hint::black_box(64000.0)); println!("{r}"); }
''', "fixed": r'''fn horizontal_bias(bh: f64) -> i16 {
    if bh > 32767.0 { 32767 } else if bh < -32768.0 { -32768 } else { bh as i16 }
}
fn main() { let r = horizontal_bias(std::hint::black_box(64000.0)); println!("{r}"); }
'''},
    "declares": {},
}

P["M-02"] = {
    "title": "Unit swap lbf·s ↔ N·s (Mars Climate Orbiter)",
    "c": {"mutant": r'''static double total_impulse(double ground_lbf_s, double nav_n_s) { return nav_n_s + ground_lbf_s; }
int main(void) { volatile double g = 1.0, n = 0.0; volatile double t = total_impulse(g, n); (void)t; return 0; }
''', "fixed": r'''static double total_impulse(double ground_lbf_s, double nav_n_s) { return nav_n_s + ground_lbf_s * 4.448222; }
int main(void) { volatile double g = 1.0, n = 0.0; volatile double t = total_impulse(g, n); (void)t; return 0; }
'''},
    "ada": {"mutant": ada("   type Newton_Second is new Long_Float;\n   type Pound_Second is new Long_Float;\n"
                          "   function Total_Impulse (Ground : Pound_Second; Nav : Newton_Second) return Newton_Second;\n",
                          "   function Total_Impulse (Ground : Pound_Second; Nav : Newton_Second) return Newton_Second is\n"
                          "     (Nav + Ground);\n",
                          "   T : Newton_Second;\n",
                          "   T := Total_Impulse (Pound_Second (K + 1), 0.0);\n   if T = 0.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   type Newton_Second is new Long_Float;\n   type Pound_Second is new Long_Float;\n"
                         "   function Total_Impulse (Ground : Pound_Second; Nav : Newton_Second) return Newton_Second;\n",
                         "   function Total_Impulse (Ground : Pound_Second; Nav : Newton_Second) return Newton_Second is\n"
                         "     (Nav + Newton_Second (Ground) * 4.448222);\n",
                         "   T : Newton_Second;\n",
                         "   T := Total_Impulse (Pound_Second (K + 1), 0.0);\n   if T = 0.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''#[derive(Clone, Copy, Debug)] struct NewtonSec(f64);
#[derive(Clone, Copy, Debug)] struct PoundSec(f64);
impl std::ops::Add for NewtonSec { type Output = NewtonSec; fn add(self, o: NewtonSec) -> NewtonSec { NewtonSec(self.0 + o.0) } }
fn total_impulse(ground: PoundSec, nav: NewtonSec) -> NewtonSec { nav + ground }
fn main() { println!("{:?}", total_impulse(PoundSec(1.0), NewtonSec(0.0))); }
''', "fixed": r'''#[derive(Clone, Copy, Debug)] struct NewtonSec(f64);
#[derive(Clone, Copy, Debug)] struct PoundSec(f64);
impl std::ops::Add for NewtonSec { type Output = NewtonSec; fn add(self, o: NewtonSec) -> NewtonSec { NewtonSec(self.0 + o.0) } }
fn total_impulse(ground: PoundSec, nav: NewtonSec) -> NewtonSec { nav + NewtonSec(ground.0 * 4.448_222) }
fn main() { println!("{:?}", total_impulse(PoundSec(1.0), NewtonSec(0.0))); }
'''},
    "declares": {"ada": True, "rust": True},
}

P["M-03"] = {
    "title": "Frame swap Body ↔ ECI",
    "c": {"mutant": r'''static double combine(double a_eci, double b_body) { return a_eci + b_body; }
int main(void) { volatile double a = 1.0, b = 2.0; volatile double r = combine(a, b); (void)r; return 0; }
''', "fixed": r'''static double combine(double a_eci, double b_eci) { return a_eci + b_eci; }
int main(void) { volatile double a = 1.0, b = 2.0; volatile double r = combine(a, b); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   type Vel_ECI is new Long_Float;\n   type Vel_Body is new Long_Float;\n"
                          "   function Combine (A : Vel_ECI; B : Vel_Body) return Vel_ECI;\n",
                          "   function Combine (A : Vel_ECI; B : Vel_Body) return Vel_ECI is (A + B);\n",
                          "   R : Vel_ECI;\n",
                          "   R := Combine (Vel_ECI (K), 2.0);\n   if R = 0.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   type Vel_ECI is new Long_Float;\n   type Vel_Body is new Long_Float;\n"
                         "   function Combine (A : Vel_ECI; B : Vel_ECI) return Vel_ECI;\n",
                         "   function Combine (A : Vel_ECI; B : Vel_ECI) return Vel_ECI is (A + B);\n",
                         "   R : Vel_ECI;\n",
                         "   R := Combine (Vel_ECI (K), 2.0);\n   if R = 0.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''#[derive(Clone, Copy, Debug)] struct VelEci(f64);
#[derive(Clone, Copy, Debug)] struct VelBody(f64);
impl std::ops::Add for VelEci { type Output = VelEci; fn add(self, o: VelEci) -> VelEci { VelEci(self.0 + o.0) } }
fn combine(a: VelEci, b: VelBody) -> VelEci { a + b }
fn main() { println!("{:?}", combine(VelEci(1.0), VelBody(2.0))); }
''', "fixed": r'''#[derive(Clone, Copy, Debug)] struct VelEci(f64);
#[derive(Clone, Copy, Debug)] struct VelBody(f64);
impl std::ops::Add for VelEci { type Output = VelEci; fn add(self, o: VelEci) -> VelEci { VelEci(self.0 + o.0) } }
fn combine(a: VelEci, b: VelEci) -> VelEci { a + b }
fn main() { let _unused = VelBody(0.0).0; println!("{:?}", combine(VelEci(1.0), VelEci(2.0))); }
'''},
    "declares": {"ada": True, "rust": True},
}

P["M-04"] = {
    "title": "Signed 32-bit centisecond counter overflow (Boeing 787 GCU)",
    "c": {"mutant": r'''#include <stdint.h>
static int32_t tick(int32_t cs) { return cs + 1; }
int main(void) { volatile int32_t cs = INT32_MAX; volatile int32_t r = tick(cs); (void)r; return 0; }
''', "fixed": r'''#include <stdint.h>
static int32_t tick(int32_t cs) { if (cs == INT32_MAX) { return INT32_MAX; } return cs + 1; }
int main(void) { volatile int32_t cs = INT32_MAX; volatile int32_t r = tick(cs); (void)r; return 0; }
'''},
    "ada": {"mutant": ada("   subtype Centisec is Integer_32 range 0 .. Integer_32'Last;\n"
                          "   function Tick (CS : Centisec) return Centisec;\n",
                          "   function Tick (CS : Centisec) return Centisec is (CS + 1);\n",
                          "   R : Centisec;\n",
                          "   R := Tick (Centisec'Last - Integer_32 (K));\n   if R = 0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   subtype Centisec is Integer_32 range 0 .. Integer_32'Last;\n"
                         "   function Tick (CS : Centisec) return Centisec;\n",
                         "   function Tick (CS : Centisec) return Centisec is (if CS = Centisec'Last then CS else CS + 1);\n",
                         "   R : Centisec;\n",
                         "   R := Tick (Centisec'Last - Integer_32 (K));\n   if R = 0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn tick(cs: i32) -> i32 { cs + 1 }
fn main() { println!("{}", tick(std::hint::black_box(i32::MAX))); }
''', "fixed": r'''fn tick(cs: i32) -> i32 { if cs == i32::MAX { i32::MAX } else { cs + 1 } }
fn main() { println!("{}", tick(std::hint::black_box(i32::MAX))); }
'''},
    "declares": {},
}

P["M-05"] = {
    "title": "Epoch mix MET ↔ TAI (Deep Impact)",
    "c": {"mutant": r'''static double now_met(void) { return 100.0; }
static double now_tai(void) { return 1.0e9; }
static double elapsed(void) { return now_tai() - now_met(); }
int main(void) { volatile double e = elapsed(); (void)e; return 0; }
''', "fixed": r'''static double now_tai(void) { return 1.0e9; }
static double elapsed(double start_tai) { return now_tai() - start_tai; }
int main(void) { volatile double s = 0.9e9; volatile double e = elapsed(s); (void)e; return 0; }
'''},
    "ada": {"mutant": ada("   type MET_Time is new Long_Float;\n   type TAI_Time is new Long_Float;\n"
                          "   function Elapsed (Now : TAI_Time; Start : MET_Time) return Long_Float;\n",
                          "   function Elapsed (Now : TAI_Time; Start : MET_Time) return Long_Float is\n"
                          "     (Long_Float (Now - Start));\n",
                          "   E : Long_Float;\n",
                          "   E := Elapsed (1.0E9, MET_Time (100 + K));\n   if E = 0.0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   type MET_Time is new Long_Float;\n   type TAI_Time is new Long_Float;\n"
                         "   function Elapsed (Now : TAI_Time; Start : TAI_Time) return Long_Float;\n",
                         "   function Elapsed (Now : TAI_Time; Start : TAI_Time) return Long_Float is\n"
                         "     (Long_Float (Now - Start));\n",
                         "   E : Long_Float;\n",
                         "   E := Elapsed (1.0E9, TAI_Time (100 + K));\n   if E = 0.0 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''#[derive(Clone, Copy)] struct Met(f64);
#[derive(Clone, Copy)] struct Tai(f64);
impl std::ops::Sub for Tai { type Output = f64; fn sub(self, o: Tai) -> f64 { self.0 - o.0 } }
fn elapsed(now: Tai, start: Met) -> f64 { now - start }
fn main() { println!("{}", elapsed(Tai(1.0e9), Met(100.0))); }
''', "fixed": r'''#[derive(Clone, Copy)] struct Met(f64);
#[derive(Clone, Copy)] struct Tai(f64);
impl std::ops::Sub for Tai { type Output = f64; fn sub(self, o: Tai) -> f64 { self.0 - o.0 } }
fn elapsed(now: Tai, start: Tai) -> f64 { now - start }
fn main() { let _ = Met(0.0).0; println!("{}", elapsed(Tai(1.0e9), Tai(100.0))); }
'''},
    "declares": {"ada": True, "rust": True},
}

P["M-06"] = {
    "title": "Buffer over-read trusting a length field (CCSDS class)",
    "c": {"mutant": r'''#include <stdint.h>
static uint8_t payload_byte(const uint8_t *buf, uint16_t data_length) { return buf[data_length]; }
int main(void) { uint8_t b[16] = {7}; volatile uint16_t len = 40000u; volatile uint8_t v = payload_byte(b, len); (void)v; return 0; }
''', "fixed": r'''#include <stdint.h>
static uint8_t payload_byte(const uint8_t *buf, uint16_t data_length) { if (data_length >= 16u) { return 0u; } return buf[data_length]; }
int main(void) { uint8_t b[16] = {7}; volatile uint16_t len = 40000u; volatile uint8_t v = payload_byte(b, len); (void)v; return 0; }
'''},
    "ada": {"mutant": ada("   type Buffer is array (Natural range 0 .. 15) of Unsigned_8;\n"
                          "   function Payload_Byte (Buf : Buffer; Len : Unsigned_16) return Unsigned_8;\n",
                          "   function Payload_Byte (Buf : Buffer; Len : Unsigned_16) return Unsigned_8 is (Buf (Natural (Len)));\n",
                          "   B : constant Buffer := (others => 7);\n   V : Unsigned_8;\n",
                          "   V := Payload_Byte (B, 40000 + Unsigned_16 (K));\n   if V = 0 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada("   type Buffer is array (Natural range 0 .. 15) of Unsigned_8;\n"
                         "   function Payload_Byte (Buf : Buffer; Len : Unsigned_16) return Unsigned_8;\n",
                         "   function Payload_Byte (Buf : Buffer; Len : Unsigned_16) return Unsigned_8 is\n"
                         "     (if Len >= 16 then 0 else Buf (Natural (Len)));\n",
                         "   B : constant Buffer := (others => 7);\n   V : Unsigned_8;\n",
                         "   V := Payload_Byte (B, 40000 + Unsigned_16 (K));\n   if V = 1 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''fn payload_byte(buf: &[u8; 16], data_length: u16) -> u8 { buf[usize::from(data_length)] }
fn main() { let b = [7u8; 16]; println!("{}", payload_byte(&b, std::hint::black_box(40000))); }
''', "fixed": r'''fn payload_byte(buf: &[u8; 16], data_length: u16) -> u8 { if data_length >= 16 { 0 } else { buf[usize::from(data_length)] } }
fn main() { let b = [7u8; 16]; println!("{}", payload_byte(&b, std::hint::black_box(40000))); }
'''},
    "declares": {},
}

_WIRE_SPEC = ("   package Wire is\n      type Data_Length is private;\n"
              "      function From_Raw (Raw : Natural) return Data_Length with Pre => Raw <= 65535;\n"
              "      function Decode (W : Data_Length) return Positive;\n"
              "   private\n      type Data_Length is range 0 .. 65535;\n"
              "      function From_Raw (Raw : Natural) return Data_Length is (Data_Length (Raw));\n"
              "      function Decode (W : Data_Length) return Positive is (Positive (W) + 1);\n   end Wire;\n")
P["M-07"] = {
    "title": "CCSDS length field: '+1' forgotten (encoding in two places)",
    "c": {"mutant": r'''#include <stdint.h>
static uint32_t data_field_octets(uint16_t raw) { return raw; }
int main(void) { volatile uint16_t raw = 5u; volatile uint32_t n = data_field_octets(raw); (void)n; return 0; }
''', "fixed": r'''#include <stdint.h>
static uint32_t data_field_octets(uint16_t raw) { return (uint32_t)raw + 1u; }
int main(void) { volatile uint16_t raw = 5u; volatile uint32_t n = data_field_octets(raw); (void)n; return 0; }
'''},
    "ada": {"mutant": ada(_WIRE_SPEC + "   function Data_Field_Octets (Raw : Wire.Data_Length) return Positive;\n",
                          "   function Data_Field_Octets (Raw : Wire.Data_Length) return Positive is (Positive (Raw));\n",
                          "   N : Positive;\n",
                          "   N := Data_Field_Octets (Wire.From_Raw (5 + K));\n   if N = 1 then Set_Exit_Status (2); end if;\n"),
            "fixed": ada(_WIRE_SPEC + "   function Data_Field_Octets (Raw : Wire.Data_Length) return Positive;\n",
                         "   function Data_Field_Octets (Raw : Wire.Data_Length) return Positive is (Wire.Decode (Raw));\n",
                         "   N : Positive;\n",
                         "   N := Data_Field_Octets (Wire.From_Raw (5 + K));\n   if N = 1 then Set_Exit_Status (2); end if;\n")},
    "rust": {"mutant": r'''mod wire {
    #[derive(Clone, Copy)] pub struct DataLength(u16);
    impl DataLength { pub fn from_raw(r: u16) -> Self { DataLength(r) } pub fn decode(self) -> u32 { u32::from(self.0) + 1 } }
}
fn data_field_octets(raw: wire::DataLength) -> u32 { u32::from(raw.0) }
fn main() { println!("{}", data_field_octets(wire::DataLength::from_raw(5))); }
''', "fixed": r'''mod wire {
    #[derive(Clone, Copy)] pub struct DataLength(u16);
    impl DataLength { pub fn from_raw(r: u16) -> Self { DataLength(r) } pub fn decode(self) -> u32 { u32::from(self.0) + 1 } }
}
fn data_field_octets(raw: wire::DataLength) -> u32 { raw.decode() }
fn main() { println!("{}", data_field_octets(wire::DataLength::from_raw(5))); }
'''},
    "declares": {"ada": True, "rust": True},
}

_BUS_BODY = ('''package body Bus with SPARK_Mode is
   protected body Lock is
      procedure Claim (Ok : out Boolean) is
      begin
         Ok := not Busy;
         Busy := True;
      end Claim;
   end Lock;
end Bus;
''')
_JORVIK = "pragma Profile (Jorvik);\npragma Partition_Elaboration_Policy (Sequential);\n"
_BUS_MAIN = ('''with Bus;
procedure Main with SPARK_Mode, Priority => 20 is
   Ok : Boolean;
begin
   Bus.Lock.Claim (Ok);
   pragma Assert (Ok or not Ok);
end Main;
''')
P["M-08"] = {
    "title": "Wrong hand-written ceiling priority → priority inversion (Mars Pathfinder)",
    "c": {"mutant": r'''#include <pthread.h>
static pthread_mutex_t bus = PTHREAD_MUTEX_INITIALIZER;
static int busy;
static int claim(void) { int ok; pthread_mutex_lock(&bus); ok = !busy; busy = 1; pthread_mutex_unlock(&bus); return ok; }
int main(void) { volatile int ok = claim(); (void)ok; return 0; }
''', "fixed": r'''#include <pthread.h>
static pthread_mutex_t bus;
static int busy;
static int claim(void) { int ok; pthread_mutex_lock(&bus); ok = !busy; busy = 1; pthread_mutex_unlock(&bus); return ok; }
int main(void) {
    pthread_mutexattr_t a; pthread_mutexattr_init(&a);
    pthread_mutexattr_setprotocol(&a, PTHREAD_PRIO_INHERIT);
    pthread_mutex_init(&bus, &a);
    volatile int ok = claim(); (void)ok; return 0; }
'''},
    "ada": {"mutant": {"main.adb": _BUS_MAIN, "bus.adb": _BUS_BODY,
                       "bus.ads": '''package Bus with SPARK_Mode is
   protected Lock with Priority => 10 is
      procedure Claim (Ok : out Boolean);
   private
      Busy : Boolean := False;
   end Lock;
end Bus;
''', "gnat.adc": "pragma Locking_Policy (Ceiling_Locking);\n", "spark.adc": _JORVIK},
            "fixed": {"main.adb": _BUS_MAIN, "bus.adb": _BUS_BODY,
                      "bus.ads": '''package Bus with SPARK_Mode is
   protected Lock with Priority => 20 is
      procedure Claim (Ok : out Boolean);
   private
      Busy : Boolean := False;
   end Lock;
end Bus;
''', "gnat.adc": "pragma Locking_Policy (Ceiling_Locking);\n", "spark.adc": _JORVIK}},
    "rust": {"mutant": r'''use std::sync::Mutex;
static BUS: Mutex<bool> = Mutex::new(false);
fn claim() -> bool { let mut b = BUS.lock().unwrap(); let ok = !*b; *b = true; ok }
fn main() { println!("{}", claim()); }
''', "fixed": r'''use std::sync::Mutex;
static BUS: Mutex<bool> = Mutex::new(false);
fn claim() -> bool { let mut b = BUS.lock().unwrap(); let ok = !*b; *b = true; ok }
fn main() { println!("{}", claim()); }
'''},
    "declares": {"ada": True},
    "note": "C and Rust have no notion of ceiling or priority in the standard language: the error cannot be expressed or checked (✗).",
}

P["M-09"] = {
    "title": "Unprotected state shared by two tasks (Therac-25 class)",
    "c": {"mutant": r'''#include <pthread.h>
static double rate_est;
static void *gnc(void *a) { (void)a; rate_est = rate_est * 0.9; return 0; }
int main(void) { pthread_t t; pthread_create(&t, 0, gnc, 0); volatile double x = rate_est; (void)x; pthread_join(t, 0); return 0; }
''', "fixed": r'''#include <pthread.h>
static double rate_est;
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static void *gnc(void *a) { (void)a; pthread_mutex_lock(&m); rate_est = rate_est * 0.9; pthread_mutex_unlock(&m); return 0; }
int main(void) { pthread_t t; pthread_create(&t, 0, gnc, 0); pthread_mutex_lock(&m); volatile double x = rate_est; pthread_mutex_unlock(&m); (void)x; pthread_join(t, 0); return 0; }
'''},
    "ada": {"mutant": {"main.adb": '''with Est;
procedure Main with SPARK_Mode is
begin
   null;
end Main;
''', "est.ads": '''package Est with SPARK_Mode is
   pragma Elaborate_Body;
   Rate_Est : Long_Float := 0.0;
   task Gnc with Priority => 20;
   task Tlm with Priority => 10;
end Est;
''', "est.adb": '''package body Est with SPARK_Mode is
   task body Gnc is
   begin
      loop
         Rate_Est := Rate_Est * 0.9;
      end loop;
   end Gnc;
   task body Tlm is
      X : Long_Float;
   begin
      loop
         X := Rate_Est;
         pragma Assert (X = X);
      end loop;
   end Tlm;
end Est;
''', "spark.adc": _JORVIK}, "fixed": {"main.adb": '''with Est;
procedure Main with SPARK_Mode is
begin
   null;
end Main;
''', "est.ads": '''package Est with SPARK_Mode is
   pragma Elaborate_Body;
   protected Store with Priority => 20 is
      procedure Decay;
      function Get return Long_Float;
   private
      Rate_Est : Long_Float := 0.0;
   end Store;
   task Gnc with Priority => 20;
   task Tlm with Priority => 10;
end Est;
''', "est.adb": '''package body Est with SPARK_Mode is
   protected body Store is
      procedure Decay is
      begin
         Rate_Est := Rate_Est * 0.9;
      end Decay;
      function Get return Long_Float is (Rate_Est);
   end Store;
   task body Gnc is
   begin
      loop
         Store.Decay;
      end loop;
   end Gnc;
   task body Tlm is
      X : Long_Float;
   begin
      loop
         X := Store.Get;
         pragma Assert (X = X);
      end loop;
   end Tlm;
end Est;
''', "spark.adc": _JORVIK}},
    "rust": {"mutant": r'''use std::thread;
fn main() {
    let mut rate_est = 0.0_f64;
    let h = thread::spawn(|| { rate_est *= 0.9; });
    let x = rate_est;
    h.join().unwrap();
    println!("{x}");
}
''', "fixed": r'''use std::sync::{Arc, Mutex};
use std::thread;
fn main() {
    let rate_est = Arc::new(Mutex::new(0.0_f64));
    let r2 = Arc::clone(&rate_est);
    let h = thread::spawn(move || { *r2.lock().unwrap() *= 0.9; });
    let x = *rate_est.lock().unwrap();
    h.join().unwrap();
    println!("{x}");
}
'''},
    "declares": {},
    "no_run": {"ada"},
}
