package Wl_Ccsds with SPARK_Mode, Pure is
   subtype Byte is Integer range 0 .. 255;
   subtype Len is Integer range 0 .. 65535;
   subtype Result is Integer range -3 .. 65542;
   function Ccsds (B0, B1, B2, B3, B4, B5 : Byte; Rx_Len : Len) return Result
     with Export, Convention => C, External_Name => "f_wl_ccsds";
end Wl_Ccsds;
