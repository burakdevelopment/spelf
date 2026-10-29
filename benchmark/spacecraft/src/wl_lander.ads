with Interfaces.C;
package Wl_Lander with SPARK_Mode, Preelaborate is
   subtype Status is Interfaces.C.int range 0 .. 3;
   subtype Alt_M is Float range 0.0 .. 2500.0;
   subtype Vz_T is Float range -80.0 .. 10.0;
   subtype Throttle is Float range 0.0 .. 1.0;
   type Leg_R is record
      S : Status;
      V : Interfaces.C.C_bool;
   end record;
   pragma Convention (C_Pass_By_Copy, Leg_R);
   type Alt_R is record
      S : Status;
      V : Alt_M;
   end record;
   pragma Convention (C_Pass_By_Copy, Alt_R);
   type Debounce is record
      Count : Integer range 0 .. 5;
      Need  : Integer range 1 .. 5;
      Set   : Boolean;
   end record;
   Armed     : Debounce := (0, 5, False);
   Touchdown : Debounce := (0, 3, False);
   function Lander_Step (Alt : Alt_R; Vz : Vz_T; L1, L2, L3 : Leg_R) return Throttle
     with Side_Effects, Export, Convention => C, External_Name => "f_lander_step",
          Global => (In_Out => (Armed, Touchdown));
end Wl_Lander;
