import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "multilang"))
import run as ML

LIB_ADS = """package Spelf_Mech with SPARK_Mode is
   type Suspect_Kind is (Valid, Saturated, Stale, Invalid);
   type Suspect (Kind : Suspect_Kind := Invalid) is record
      case Kind is
         when Invalid => null;
         when others  => Value : Long_Float;
      end case;
   end record;

   subtype Time is Long_Float range 0.0 .. 1.0E9;
   type Held is private;
   procedure Init (H : out Held; Init_Value : Long_Float; Max_Age : Time);
   procedure Refresh (H : in out Held; V : Long_Float; Now : Time);
   type Held_Kind is (Fresh, Expired);
   type Held_Read (Kind : Held_Kind := Expired) is record
      case Kind is
         when Fresh   => Value : Long_Float;
         when Expired => null;
      end case;
   end record;
   function Read (H : Held; Now : Time) return Held_Read;

   type Latch is private;
   procedure Init (L : out Latch; Persists_For : Positive);
   procedure Feed (L : in out Latch; C : Boolean);
   function Is_Set (L : Latch) return Boolean;

   type Clamp_Kind is (Unclamped, Clamped);
   type Clamp_Result (Kind : Clamp_Kind := Clamped) is record
      case Kind is
         when Unclamped => U_Value : Long_Float;
         when Clamped   => C_Value : Long_Float;
      end case;
   end record;
   function Saturate (X, Lo, Hi : Long_Float) return Clamp_Result with Pre => Lo <= Hi;

   type Rounding is (Nearest_Even, Toward_Zero, Floor, Ceil);
   subtype Raw is Long_Long_Integer range -(2 ** 40) .. 2 ** 40;
   function Requantize (X : Raw; Mode : Rounding) return Raw;
private
   type Held is record
      Value   : Long_Float := 0.0;
      Stamp   : Time := 0.0;
      Max_Age : Time := 0.0;
      Valid   : Boolean := False;
   end record;
   type Latch is record
      Count : Natural := 0;
      Need  : Positive := 1;
      Set   : Boolean := False;
   end record;
end Spelf_Mech;
"""

LIB_ADB = """package body Spelf_Mech with SPARK_Mode is
   procedure Init (H : out Held; Init_Value : Long_Float; Max_Age : Time) is
   begin
      H := (Value => Init_Value, Stamp => 0.0, Max_Age => Max_Age, Valid => False);
   end Init;

   procedure Refresh (H : in out Held; V : Long_Float; Now : Time) is
   begin
      H.Value := V;
      H.Stamp := Now;
      H.Valid := True;
   end Refresh;

   function Read (H : Held; Now : Time) return Held_Read is
   begin
      if H.Valid and then Now >= H.Stamp and then Now - H.Stamp <= H.Max_Age then
         return (Kind => Fresh, Value => H.Value);
      end if;
      return (Kind => Expired);
   end Read;

   procedure Init (L : out Latch; Persists_For : Positive) is
   begin
      L := (Count => 0, Need => Persists_For, Set => False);
   end Init;

   procedure Feed (L : in out Latch; C : Boolean) is
   begin
      if C then
         if L.Count < L.Need then
            L.Count := L.Count + 1;
         end if;
         if L.Count >= L.Need then
            L.Set := True;
         end if;
      else
         L.Count := 0;
      end if;
   end Feed;

   function Is_Set (L : Latch) return Boolean is (L.Set);

   function Saturate (X, Lo, Hi : Long_Float) return Clamp_Result is
   begin
      if X < Lo then
         return (Kind => Clamped, C_Value => Lo);
      elsif X > Hi then
         return (Kind => Clamped, C_Value => Hi);
      end if;
      return (Kind => Unclamped, U_Value => X);
   end Saturate;

   function Requantize (X : Raw; Mode : Rounding) return Raw is
      P : constant := 256;
      Q : Long_Long_Integer range -(2 ** 33) .. 2 ** 33 := X / P;
      R : constant Long_Long_Integer range -255 .. 255 := X rem P;
   begin
      case Mode is
         when Toward_Zero => null;
         when Floor =>
            if R < 0 then
               Q := Q - 1;
            end if;
         when Ceil =>
            if R > 0 then
               Q := Q + 1;
            end if;
         when Nearest_Even =>
            if 2 * abs R > P or else (2 * abs R = P and then Q rem 2 /= 0) then
               Q := (if X >= 0 then Q + 1 else Q - 1);
            end if;
      end case;
      return Q;
   end Requantize;
end Spelf_Mech;
"""

LIB_ADB_CASE = LIB_ADB.replace("package body Spelf_Mech with SPARK_Mode is",
                               "package body Spelf_Mech with SPARK_Mode => Off is")

MAIN = """with Case_L;
procedure Main with SPARK_Mode => Off is
begin
   null;
end Main;
"""

def case(spec_m, body_m, spec_f, body_f):
    def files(spec, body):
        return {"spelf_mech.ads": LIB_ADS, "spelf_mech.adb": LIB_ADB_CASE, "main.adb": MAIN,
                "case_l.ads": "with Spelf_Mech; use Spelf_Mech;\npackage Case_L with SPARK_Mode is\n" + spec + "end Case_L;\n",
                "case_l.adb": "package body Case_L with SPARK_Mode is\n" + body + "end Case_L;\n"}
    return {"mutant": files(spec_m, body_m), "fixed": files(spec_f, body_f)}

SENSOR = "   function Read_Sensor return Suspect with Import, Convention => C, Global => null;\n"
PORT = "   Port : Long_Float with Volatile, Async_Writers, Import, Convention => C;\n"

CASES = {
    "L-10a": ("with the Suspect library: the value is read without checking the tag (M-10)", "a", case(
        SENSOR + "   function Rate_Cmd return Long_Float;\n",
        "   function Rate_Cmd return Long_Float is\n      S : constant Suspect := Read_Sensor;\n   begin\n"
        "      return S.Value * 0.5;\n   end Rate_Cmd;\n",
        SENSOR + "   function Rate_Cmd return Long_Float;\n",
        "   function Rate_Cmd return Long_Float is\n      S : constant Suspect := Read_Sensor;\n   begin\n"
        "      case S.Kind is\n         when Valid => return S.Value * 0.5;\n         when others => return 0.0;\n"
        "      end case;\n   end Rate_Cmd;\n")),
    "L-11b": ("WITHOUT the Held library the last good value persists without an age limit (M-11)", "b", case(
        SENSOR + "   Last_Good : Long_Float := 0.0;\n   procedure Step with Global => (In_Out => Last_Good);\n"
        "   function Attitude return Long_Float with Global => Last_Good;\n",
        "   procedure Step is\n      S : constant Suspect := Read_Sensor;\n   begin\n"
        "      if S.Kind = Valid then\n         Last_Good := S.Value;\n      end if;\n   end Step;\n"
        "   function Attitude return Long_Float is (Last_Good);\n",
        SENSOR + "   Att : Held;\n   procedure Step (Now : Time) with Global => (In_Out => Att);\n"
        "   function Attitude (Now : Time) return Long_Float with Global => Att;\n",
        "   procedure Step (Now : Time) is\n      S : constant Suspect := Read_Sensor;\n   begin\n"
        "      if S.Kind = Valid then\n         Refresh (Att, S.Value, Now);\n      end if;\n   end Step;\n"
        "   function Attitude (Now : Time) return Long_Float is\n      R : constant Held_Read := Read (Att, Now);\n"
        "   begin\n      case R.Kind is\n         when Fresh => return R.Value;\n         when Expired => return 0.0;\n"
        "      end case;\n   end Attitude;\n")),
    "L-11c": ("with an information-flow contract (Depends): sensor as volatile external state, honest contract on both (M-11)", "b", case(
        PORT + "   Last_Good : Long_Float := 0.0;\n"
        "   procedure Step with Global => (Input => Port, In_Out => Last_Good), Depends => (Last_Good =>+ Port);\n",
        "   procedure Step is\n      V : constant Long_Float := Port;\n   begin\n"
        "      if V in -10.0 .. 10.0 then\n         Last_Good := V;\n      end if;\n   end Step;\n",
        PORT + "   Att : Held;\n"
        "   procedure Step (Now : Time) with Global => (Input => Port, In_Out => Att), Depends => (Att =>+ (Port, Now));\n",
        "   procedure Step (Now : Time) is\n      V : constant Long_Float := Port;\n   begin\n"
        "      if V in -10.0 .. 10.0 then\n         Refresh (Att, V, Now);\n      end if;\n   end Step;\n")),
    "L-12b": ("WITHOUT Latch a transient contact signal is latched (M-12, Mars Polar Lander)", "b", case(
        "   Landed : Boolean := False;\n   procedure Step (Touch : Boolean) with Global => (In_Out => Landed);\n",
        "   procedure Step (Touch : Boolean) is\n   begin\n      if Touch then\n         Landed := True;\n"
        "      end if;\n   end Step;\n",
        "   Touch_Latch : Latch;\n   procedure Step (Touch : Boolean) with Global => (In_Out => Touch_Latch);\n"
        "   function Landed return Boolean with Global => Touch_Latch;\n",
        "   procedure Step (Touch : Boolean) is\n   begin\n      Feed (Touch_Latch, Touch);\n   end Step;\n"
        "   function Landed return Boolean is (Is_Set (Touch_Latch));\n")),
    "L-18a": ("with the Clamped library: the clamping information is ignored and the value read (M-18)", "a", case(
        "   Integ : Long_Float range -0.5 .. 0.5 := 0.0;\n"
        "   procedure Step (Err : Long_Float; Cmd : out Long_Float) with Global => (In_Out => Integ),\n"
        "     Pre => Err in -1.0 .. 1.0;\n",
        "   procedure Step (Err : Long_Float; Cmd : out Long_Float) is\n"
        "      C : constant Clamp_Result := Saturate (Integ + Err, -1.0, 1.0);\n   begin\n"
        "      Cmd := C.U_Value;\n      Integ := Long_Float'Max (-0.5, Long_Float'Min (0.5, Integ + 0.01 * Err));\n"
        "   end Step;\n",
        "   Integ : Long_Float range -0.5 .. 0.5 := 0.0;\n"
        "   procedure Step (Err : Long_Float; Cmd : out Long_Float) with Global => (In_Out => Integ),\n"
        "     Pre => Err in -1.0 .. 1.0;\n",
        "   procedure Step (Err : Long_Float; Cmd : out Long_Float) is\n"
        "      C : constant Clamp_Result := Saturate (Integ + Err, -1.0, 1.0);\n   begin\n"
        "      case C.Kind is\n         when Unclamped =>\n            Cmd := C.U_Value;\n"
        "            Integ := Long_Float'Max (-0.5, Long_Float'Min (0.5, Integ + 0.01 * Err));\n"
        "         when Clamped =>\n            Cmd := C.C_Value;\n      end case;\n"
        "   end Step;\n")),
    "L-24b": ("WITHOUT Requantize: implicit rounding in fixed point (M-24)", "b", case(
        "   type Out_T is delta 2.0 ** (-8) range -128.0 .. 128.0 - 2.0 ** (-8);\n"
        "   function Step (E, G : Long_Float) return Out_T with Pre => abs E <= 8.0 and abs G <= 2.0;\n",
        "   function Step (E, G : Long_Float) return Out_T is (Out_T (E * G));\n",
        "   type Out_T is delta 2.0 ** (-8) range -128.0 .. 128.0 - 2.0 ** (-8);\n"
        "   function Step (E, G : Long_Float) return Out_T with Pre => abs E <= 8.0 and abs G <= 2.0;\n",
        "   function Step (E, G : Long_Float) return Out_T is\n"
        "     (Out_T (Long_Float (Requantize (Raw (Long_Float'Truncation (E * G * 65536.0)), Nearest_Even)) / 256.0));\n")),
}

def contract_lines(text: str) -> int:
    keys = ("Pre ", "Pre =>", "Post", "Global", "Depends", "Dynamic_Predicate", "Loop_Invariant", "Contract_Cases")
    return sum(1 for l in text.splitlines() if any(k in l for k in keys))

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    import tempfile
    env, ok = ML.gnat_env()
    if not ok:
        print("GNAT/gnatprove missing (Alire: alr toolchain --select gnat_native gprbuild; alr install gnatprove)")
        sys.exit(2)
    out = {}
    with tempfile.TemporaryDirectory() as d:
        dd = pathlib.Path(d)
        files = {"spelf_mech.ads": LIB_ADS, "spelf_mech.adb": LIB_ADB,
                 "main.adb": "with Spelf_Mech;\nprocedure Main with SPARK_Mode => Off is\nbegin\n   null;\nend Main;\n"}
        ok_lib, msgs = ML.spark_static(files, dd)
    lib_clean = ok_lib and not msgs
    print(f"library spelf_mech: {'clean under GNATprove (AoRTE proved)' if lib_clean else 'DIAGNOSTICS: ' + '; '.join(msgs[:3])}")
    for cid, (title, q, prog) in CASES.items():
        with tempfile.TemporaryDirectory() as d:
            dd = pathlib.Path(d)
            (dd / "m").mkdir()
            (dd / "f").mkdir()
            sm = ML.spark_static(prog["mutant"], dd / "m")
            sf = ML.spark_static(prog["fixed"], dd / "f")
        verdict, ev = ML.classify(sm, sf, None, None, True)
        out[cid] = {"title": title, "question": q, "verdict": verdict, "evidence": ev,
                    "contract_lines_fixed": contract_lines(prog["fixed"]["case_l.ads"] + prog["fixed"]["case_l.adb"])}
        print(f"{cid}: {verdict:2}  {title}\n       {ev[:150]}")
    (HERE / "results.json").write_text(json.dumps({"library_clean": lib_clean, "cases": out}, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
