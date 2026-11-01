"""Conservative advance-width measurement of the bundled static Kanit font, no new dependency."""
import struct
from pathlib import Path

class Metrics:
    def __init__(self,path):
        self.raw=Path(path).read_bytes();r=self.raw
        u16=lambda p:struct.unpack_from('>H',r,p)[0]
        self.u16=u16
        self.tables={r[p:p+4].decode('ascii'):struct.unpack_from('>II',r,p+8) for p in range(12,12+16*u16(4),16)}
        self.units=u16(self.tables['head'][0]+18)
        self.metrics=u16(self.tables['hhea'][0]+34);self.hmtx=self.tables['hmtx'][0]
        cmap=self.tables['cmap'][0];self.cmap=None
        for p in range(cmap+4,cmap+4+u16(cmap+2)*8,8):
            platform,encoding,offset=struct.unpack_from('>HHI',r,p)
            if platform in (0,3) and u16(cmap+offset)==4:self.cmap=cmap+offset
        if self.cmap is None:raise ValueError('Bundled font has no Unicode BMP cmap')
    def glyph(self,code):
        if code>65535:return 0
        r=self.raw;c=self.cmap;n=self.u16(c+6)//2;ends=c+14;starts=ends+2*n+2;deltas=starts+2*n;ranges=deltas+2*n
        for i in range(n):
            end=self.u16(ends+2*i);start=self.u16(starts+2*i)
            if start<=code<=end:
                delta=struct.unpack_from('>h',r,deltas+2*i)[0];offset=self.u16(ranges+2*i)
                glyph=self.u16(ranges+2*i+offset+2*(code-start)) if offset else code
                return (glyph+delta)&65535 if glyph else 0
        return 0
    def width(self,text,size):
        total=0
        for c in text:
            glyph=self.glyph(ord(c))
            if not glyph:raise ValueError('ฟอนต์ Kanit ไม่รองรับอักขระ '+c)
            total+=self.u16(self.hmtx+4*min(glyph,self.metrics-1))
        return total/self.units*size
