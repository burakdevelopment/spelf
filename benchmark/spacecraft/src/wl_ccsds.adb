with Interfaces; use Interfaces;
package body Wl_Ccsds with SPARK_Mode is
   function Ccsds (B0, B1, B2, B3, B4, B5 : Byte; Rx_Len : Len) return Result is
      pragma Unreferenced (B2, B3);
      W0   : Unsigned_32;
      Dlen : Integer;
   begin
      if Rx_Len < 6 then
         return -1;
      end if;
      W0 := Unsigned_32 (B0) * 256 + Unsigned_32 (B1);
      if Shift_Right (W0, 13) /= 0 then
         return -2;
      end if;
      if (W0 and 2047) = 2047 then
         return -3;
      end if;
      Dlen := B4 * 256 + B5 + 1;
      if 6 + Dlen > Rx_Len then
         return -1;
      end if;
      return 6 + Dlen;
   end Ccsds;
end Wl_Ccsds;
