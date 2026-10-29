with Interfaces; use Interfaces;
package Wl_Crc16 with SPARK_Mode, Pure is
   type Frame is array (0 .. 63) of Unsigned_8 with Convention => C;
   subtype Count is Integer range 0 .. 64;
   function Crc16 (Buf : Frame; N : Count) return Integer
     with Export, Convention => C, External_Name => "f_wl_crc16",
          Post => Crc16'Result in 0 .. 16#FFFF#;
end Wl_Crc16;
