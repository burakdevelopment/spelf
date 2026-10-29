package body Wl_Lander with SPARK_Mode is
   use type Interfaces.C.int;
   ARM_ALT : constant Float := 40.0;
   HOVER   : constant Float := 0.38;
   KV      : constant Float := 0.08;
   KH      : constant Float := 0.1;

   procedure Feed (D : in out Debounce; C : Boolean) is
   begin
      if C then
         if D.Count < D.Need then
            D.Count := D.Count + 1;
         end if;
         if D.Count >= D.Need then
            D.Set := True;
         end if;
      else
         D.Count := 0;
      end if;
   end Feed;

   function Leg (R : Leg_R) return Integer is (if R.S = 0 and then Boolean (R.V) then 1 else 0);

   function Lander_Step (Alt : Alt_R; Vz : Vz_T; L1, L2, L3 : Leg_R) return Throttle is
      Vref, V, T : Float;
      N : Integer;
   begin
      if Alt.S = 0 then
         Feed (Armed, Alt.V < ARM_ALT);
         V := 0.0 - KH * Alt.V - 1.0;
         Vref := (if V < -20.0 then -20.0 else V);
      else
         Armed.Count := 0;
         Vref := -2.0;
      end if;
      N := Leg (L1) + Leg (L2) + Leg (L3);
      Feed (Touchdown, Armed.Set and then N >= 2);
      if Touchdown.Set then
         return 0.0;
      end if;
      T := HOVER + KV * (Vref - Vz);
      return (if T < 0.1 then 0.1 elsif T > 1.0 then 1.0 else T);
   end Lander_Step;
end Wl_Lander;
