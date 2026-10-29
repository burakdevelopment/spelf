with Interfaces.C;
package Wl_Bdot with SPARK_Mode, Preelaborate is
   subtype Bmag is Float range -80.0 .. 80.0;
   subtype Dip is Float range -0.2 .. 0.2;
   subtype Rate is Float range -2000.0 .. 2000.0;
   type Dip_V is record
      X, Y, Z : Dip;
   end record with Convention => C;
   Primed        : Boolean := False;
   Bpx, Bpy, Bpz : Bmag := 0.0;
   Fx, Fy, Fz    : Rate := 0.0;
   procedure Bdot_Step (Mx, My, Mz : Bmag; Valid : Interfaces.C.C_bool; D : out Dip_V)
     with Export, Convention => C, External_Name => "f_bdot_step",
          Global => (In_Out => (Primed, Bpx, Bpy, Bpz, Fx, Fy, Fz));
end Wl_Bdot;
