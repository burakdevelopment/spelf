package body Wl_Fbw with SPARK_Mode is
   QMAX  : constant Float := 0.35;
   ALIM  : constant Float := 0.45;
   KLIM  : constant Float := 2.0;
   DT    : constant Float := 0.0125;
   RSTEP : constant Float := 0.0125;
   subtype Gain is Float range 0.0 .. 4.0;
   subtype Weight is Float range 0.0 .. 1.0;
   subtype Sched_T is Float range -4.0 .. 8.0;

   function Med3 (A, B, C : Rate) return Rate is
      Lo : constant Rate := (if A < B then A else B);
      Hi : constant Rate := (if A < B then B else A);
      M  : constant Rate := (if Hi < C then Hi else C);
   begin
      return (if Lo < M then M else Lo);
   end Med3;

   function Med3a (A, B, C : Aoa) return Aoa is
      Lo : constant Aoa := (if A < B then A else B);
      Hi : constant Aoa := (if A < B then B else A);
      M  : constant Aoa := (if Hi < C then Hi else C);
   begin
      return (if Lo < M then M else Lo);
   end Med3a;

   function Sched (Qb : Qbar; K0, K1, K2, K3 : Gain) return Sched_T is
      W : Weight;
   begin
      if Qb < 10000.0 then
         W := (Qb - 1000.0) / 9000.0;
         return K0 + (K1 - K0) * W;
      elsif Qb < 30000.0 then
         W := (Qb - 10000.0) / 20000.0;
         return K1 + (K2 - K1) * W;
      else
         W := (Qb - 30000.0) / 30000.0;
         return K2 + (K3 - K2) * W;
      end if;
   end Sched;

   function Rate_Lim (D, P : Defl) return Defl is
      Up : constant Float := P + RSTEP;
      Dn : constant Float := P - RSTEP;
   begin
      return (if D > Up then Up elsif D < Dn then Dn else D);
   end Rate_Lim;

   function Fbw_Step (Stick : Stick_T; Q1, Q2, Q3 : Rate; A1, A2, A3 : Aoa; Qb : Qbar) return Defl is
      Q      : constant Rate := Med3 (Q1, Q2, Q3);
      A      : constant Aoa := Med3a (A1, A2, A3);
      Kq     : constant Float := Sched (Qb, 1.2, 0.6, 0.35, 0.22) * 1.0;
      Ka     : constant Float := Sched (Qb, 3.0, 1.6, 0.9, 0.55);
      Ki     : constant Float := Sched (Qb, 1.5, 0.8, 0.45, 0.3);
      Excess : constant Float := (if A > ALIM then A - ALIM else 0.0);
      Qcmd   : constant Float := Stick * QMAX - KLIM * Excess;
      E      : constant Float := Qcmd - Q;
      Raw    : constant Float := Kq * E + Ki * Xi - Ka * A;
      D, Lim : Defl;
      Xn     : Float;
   begin
      if Raw >= -0.44 and then Raw <= 0.44 then
         D := Raw;
         Xn := Xi + E * DT;
         Xi := (if Xn > 1.0 then 1.0 elsif Xn < -1.0 then -1.0 else Xn);
      elsif Raw < -0.44 then
         D := -0.44;
      else
         D := 0.44;
      end if;
      Lim := Rate_Lim (D, Dprev);
      Dprev := Lim;
      return Lim;
   end Fbw_Step;
end Wl_Fbw;
