with Interfaces.C;
package Wl_Px4 with SPARK_Mode, Preelaborate is
   subtype Axis is Integer range 0 .. 2;
   subtype Rate_Err is Float range -70.0 .. 70.0;
   subtype Rate is Float range -35.0 .. 35.0;
   subtype Accel_T is Float range -500.0 .. 500.0;
   subtype Dt_T is Float range 0.000125 .. 0.02;
   subtype Integ_T is Float range -1.0 .. 1.0;
   type Integ_Array is array (Axis) of Integ_T;
   Integ : Integ_Array := (others => 0.0);
   function Axis_Torque (K : Axis; Err : Rate_Err; Rate_Sp : Rate; Accel : Accel_T) return Float
     with Export, Convention => C, External_Name => "f_axis_torque", Global => Integ;
   procedure Axis_Integrate (K : Axis; Rate_Error : Rate_Err; Dt : Dt_T; Sat_Pos, Sat_Neg : Interfaces.C.C_bool)
     with Export, Convention => C, External_Name => "f_axis_integrate", Global => (In_Out => Integ);
end Wl_Px4;
