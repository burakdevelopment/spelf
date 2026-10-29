package Wl_Fbw with SPARK_Mode, Preelaborate is
   subtype Stick_T is Float range -1.0 .. 1.0;
   subtype Rate is Float range -2.0 .. 2.0;
   subtype Aoa is Float range -0.6 .. 0.6;
   subtype Defl is Float range -0.44 .. 0.44;
   subtype Qbar is Float range 1000.0 .. 60000.0;
   subtype Integ_T is Float range -1.0 .. 1.0;
   Xi    : Integ_T := 0.0;
   Dprev : Defl := 0.0;
   function Fbw_Step (Stick : Stick_T; Q1, Q2, Q3 : Rate; A1, A2, A3 : Aoa; Qb : Qbar) return Defl
     with Side_Effects, Export, Convention => C, External_Name => "f_fbw_step",
          Global => (In_Out => (Xi, Dprev));
end Wl_Fbw;
