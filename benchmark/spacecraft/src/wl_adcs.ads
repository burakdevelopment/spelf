package Wl_Adcs with SPARK_Mode, Pure is
   subtype Q is Long_Float range -1.0 .. 1.0;
   subtype Rate is Long_Float range -0.5 .. 0.5;
   subtype Torque is Long_Float range -0.01 .. 0.01;
   subtype Axis is Integer range 0 .. 2;
   function Adcs (K : Axis; Qw, Qx, Qy, Qz, Rw, Rx, Ry, Rz : Q; Wx, Wy, Wz : Rate) return Torque
     with Export, Convention => C, External_Name => "f_wl_adcs";
end Wl_Adcs;
