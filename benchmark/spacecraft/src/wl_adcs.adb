package body Wl_Adcs with SPARK_Mode is
   KP   : constant := 0.02;
   KD   : constant := 0.15;
   TMAX : constant := 0.01;

   function Adcs (K : Axis; Qw, Qx, Qy, Qz, Rw, Rx, Ry, Rz : Q; Wx, Wy, Wz : Rate) return Torque is
      Ew : constant Long_Float := Rw * Qw + Rx * Qx + Ry * Qy + Rz * Qz;
      Ex : constant Long_Float := Rw * Qx - Rx * Qw - Ry * Qz + Rz * Qy;
      Ey : constant Long_Float := Rw * Qy + Rx * Qz - Ry * Qw - Rz * Qx;
      Ez : constant Long_Float := Rw * Qz - Rx * Qy + Ry * Qx - Rz * Qw;
      E0 : constant Long_Float := (if K = 0 then Ex elsif K = 1 then Ey else Ez);
      W0 : constant Long_Float := (if K = 0 then Wx elsif K = 1 then Wy else Wz);
      E  : constant Long_Float := (if Ew < 0.0 then 0.0 - E0 else E0);
      T  : constant Long_Float := 0.0 - KP * E - KD * W0;
   begin
      if T < -TMAX then
         return -TMAX;
      elsif T > TMAX then
         return TMAX;
      else
         return T;
      end if;
   end Adcs;
end Wl_Adcs;
