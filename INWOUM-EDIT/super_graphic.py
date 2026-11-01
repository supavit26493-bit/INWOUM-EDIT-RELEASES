"""Editable Premiere graphics based on the user's supersetting.xml specimen.

Source Text is Premiere's size-prefixed FlatBuffer, not an XML text generator.
Keep its style tables intact; relocate only the one UTF-8 string. Nothing is
inserted into existing tables, so existing relative offsets remain valid.
"""
import base64,copy,struct,uuid
import xml.etree.ElementTree as E
import bootstrap

FONT='Kanit-Bold'
SIZE=100

def _field(raw,table,index):
    vtable=table-struct.unpack_from('<i',raw,table)[0]
    length=struct.unpack_from('<H',raw,vtable)[0]
    if 4+index*2>=length:raise ValueError('Super template: missing style field')
    offset=struct.unpack_from('<H',raw,vtable+4+index*2)[0]
    if not offset:raise ValueError('Super template: empty style field')
    return table+offset

def _target(raw,pointer):return pointer+struct.unpack_from('<I',raw,pointer)[0]

def text_pointer(raw):
    if len(raw)<32 or struct.unpack_from('<I',raw,0)[0]!=len(raw)-12 or raw[8:12]!=b'\x44\x33\x22\x11':
        raise ValueError('Super template: invalid Source Text header')
    root=_target(raw,12)
    style=_target(raw,_field(raw,root,0))
    paragraphs=_target(raw,_field(raw,style,0))
    if struct.unpack_from('<I',raw,paragraphs)[0]!=1:raise ValueError('Super requires one text paragraph')
    paragraph=_target(raw,paragraphs+4)
    return _field(raw,paragraph,0)

def read_text(value):
    raw=base64.b64decode(value,validate=True);pos=_target(raw,text_pointer(raw))
    length=struct.unpack_from('<I',raw,pos)[0]
    if pos+4+length>=len(raw) or raw[pos+4+length]!=0:raise ValueError('Invalid Super string bounds')
    return raw[pos+4:pos+4+length].decode('utf8')

def replace_text(value,text):
    if '\n' in text or '\r' in text or '\x00' in text:raise ValueError('Super must be a single line')
    raw=bytearray(base64.b64decode(value,validate=True));pointer=text_pointer(raw)
    old=_target(raw,pointer);length=struct.unpack_from('<I',raw,old)[0]
    if read_text(value)==text:return value
    # Erase unreferenced text so the distributable template contains no old copy.
    raw[old+4:old+4+length]=bytes(length)
    raw.extend(bytes((-len(raw))%4));pos=len(raw);encoded=text.encode('utf8')
    raw.extend(struct.pack('<I',len(encoded))+encoded+b'\x00')
    raw.extend(bytes((-len(raw))%4))
    struct.pack_into('<I',raw,pointer,pos-pointer)
    struct.pack_into('<I',raw,0,len(raw)-12)
    result=base64.b64encode(raw).decode('ascii')
    if read_text(result)!=text:raise ValueError('Super Source Text round trip failed')
    return result

def clip(text,start,end,project,anchor=False):
    template=E.parse(bootstrap.ROOT/'assets/super-style.xml').getroot()
    item=copy.deepcopy(template.find('clipitem'));suffix=uuid.uuid4().hex
    item.set('id','inwoum-super-'+('anchor-' if anchor else '')+suffix)
    master=item.find('masterclipid')
    if master is not None:item.remove(master)
    for node in list(item.findall('logginginfo'))+list(item.findall('colorinfo')):item.remove(node)
    item.find('name').text='SUPER START · Frame 0 · Empty' if anchor else text
    for key,value in [('duration',end-start),('start',start),('end',end),('in',0),('out',end-start)]:item.find(key).text=str(value)
    rate=item.find('rate');rate.clear();rate.extend(copy.deepcopy(list(project['seq'].find('rate'))))
    for key,frame in [('pproTicksIn',0),('pproTicksOut',end-start)]:
        node=item.find(key)
        if node is not None:node.text=str(round(frame/project['fps']*254016000000))
    file=item.find('file');file.set('id','super-graphic-'+suffix)
    for key,value in [('width',project['width']),('height',project['height'])]:file.find('media/video/samplecharacteristics/'+key).text=str(value)
    effect=item.find("filter/effect[effectid='GraphicAndType']")
    effect.find('name').text='Super'
    source=effect.find("parameter[parameterid='1']/value")
    source.text=replace_text(source.text,text)
    fingerprint=effect.find("parameter[parameterid='1']/hash")
    if fingerprint is not None:fingerprint.text=str(uuid.uuid4())
    # Keep native 100pt text. Scale the graphic uniformly with image width for
    # other frame sizes; preserve its centered local baseline at that scale.
    # Downward placement belongs exclusively to its separate Basic Motion.
    pos=effect.find("parameter[parameterid='3']/value")
    parts=pos.text.split(',');x,y=map(float,parts[1].split(':'))
    width,height=float(template.get('width')),float(template.get('height'))
    factor=project['width']/width
    parts[1]=f'{.5+(x-.5)*width*factor/project["width"]:.17g}:{.5+(y-.5)*height*factor/project["height"]:.17g}'
    pos.text=','.join(parts)
    if factor!=1:
        for key in ('4','5'):
            scale=effect.find(f"parameter[parameterid='{key}']/value")
            values=scale.text.split(',');values[1]=f'{100*factor:.10g}';scale.text=','.join(values)
    if anchor:
        opacity=effect.find("parameter[parameterid='8']/value")
        values=opacity.text.split(',');values[1]='0.';opacity.text=','.join(values)
    return item

def item_text(item):
    return read_text(item.findtext("filter/effect[effectid='GraphicAndType']/parameter[parameterid='1']/value"))
