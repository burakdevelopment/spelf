package body Wl_Px4 with SPARK_Mode is
   GP  : constant Float := 0.15;
   GI  : constant Float := 0.2;
   GD  : constant Float := 0.003;
   GFF : constant Float := 0.0;
   LIM : constant Float := 0.3;

   function Axis_Torque (K : Axis; Err : Rate_Err; Rate_Sp : Rate; Accel : Accel_T) return Float is
     (GP * Err + Integ (K) - GD * Accel + GFF * Rate_Sp);

   procedure Axis_Integrate (K : Axis; Rate_Error : Rate_Err; Dt : Dt_T; Sat_Pos, Sat_Neg : Interfaces.C.C_bool) is
      E : Float := Rate_Error;
      X, Qq, I_Factor, Rate_I : Float;
   begin
      if Boolean (Sat_Pos) then
         if E > 0.0 then
            E := 0.0;
         end if;
      end if;
      if Boolean (Sat_Neg) then
         if E < 0.0 then
            E := 0.0;
         end if;
      end if;
      X := E / 6.981317007977318;
      Qq := 1.0 - X * X;
      I_Factor := (if Qq < 0.0 then 0.0 else Qq);
      Rate_I := Integ (K) + I_Factor * GI * E * Dt;
      Integ (K) := (if Rate_I > LIM then LIM elsif Rate_I < 0.0 - LIM then 0.0 - LIM else Rate_I);
   end Axis_Integrate;
end Wl_Px4;
