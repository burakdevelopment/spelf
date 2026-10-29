package body Wl_Crc16 with SPARK_Mode is
   function Crc16 (Buf : Frame; N : Count) return Integer is
      Crc : Unsigned_32 := 16#FFFF#;
   begin
      for I in Frame'Range loop
         pragma Loop_Invariant (Crc <= 16#FFFF#);
         if I < N then
            Crc := Crc xor Shift_Left (Unsigned_32 (Buf (I)), 8);
            for B in 1 .. 8 loop
               pragma Loop_Invariant (Crc <= 16#FFFF#);
               if (Crc and 16#8000#) /= 0 then
                  Crc := (Shift_Left (Crc, 1) and 16#FFFF#) xor 16#1021#;
               else
                  Crc := Shift_Left (Crc, 1) and 16#FFFF#;
               end if;
            end loop;
         end if;
      end loop;
      return Integer (Crc);
   end Crc16;
end Wl_Crc16;
