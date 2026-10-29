package body Wl_Bdot with SPARK_Mode is
   DT    : constant Float := 0.1;
   K     : constant Float := 0.05;
   ALPHA : constant Float := 0.3;
   MMAX  : constant Float := 0.2;

   function Torquer (F : Rate) return Dip is
      M : constant Float := 0.0 - K * F;
   begin
      return (if M > MMAX then MMAX elsif M < -MMAX then -MMAX else M);
   end Torquer;

   procedure Bdot_Step (Mx, My, Mz : Bmag; Valid : Interfaces.C.C_bool; D : out Dip_V) is
      Bx : constant Bmag := -My;
      By : constant Bmag := Mx;
      Bz : constant Bmag := Mz;
   begin
      if not Boolean (Valid) then
         Primed := False;
         D := (0.0, 0.0, 0.0);
         return;
      end if;
      if not Primed then
         Bpx := Bx;
         Bpy := By;
         Bpz := Bz;
         Primed := True;
         D := (0.0, 0.0, 0.0);
         return;
      end if;
      Fx := Fx + ALPHA * ((Bx - Bpx) / DT - Fx);
      Fy := Fy + ALPHA * ((By - Bpy) / DT - Fy);
      Fz := Fz + ALPHA * ((Bz - Bpz) / DT - Fz);
      Bpx := Bx;
      Bpy := By;
      Bpz := Bz;
      D := (Torquer (Fx), Torquer (Fy), Torquer (Fz));
   end Bdot_Step;
end Wl_Bdot;
