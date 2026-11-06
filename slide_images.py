
"""Offline, deterministic illustration assets. No external API/key is required."""
from io import BytesIO
import hashlib, math
from PIL import Image, ImageDraw, ImageFont

def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"):
        try: return ImageFont.truetype(path, size)
        except Exception: pass
    return ImageFont.load_default()

def make_illustration(topic, heading, accent="#3B82F6", seed=0, size=(1100,700)):
    digest=hashlib.sha256((str(topic)+str(heading)+str(seed)).encode("utf-8")).digest()
    base=(int(digest[0]),int(digest[1]),int(digest[2]))
    # Light, muted background with a distinct accent-led editorial illustration.
    bg=tuple(int(235+(c%20)) for c in base)
    im=Image.new("RGB",size,bg); d=ImageDraw.Draw(im)
    try: ac=tuple(int(accent.lstrip("#")[i:i+2],16) for i in (0,2,4))
    except Exception: ac=(50,120,190)
    w,h=size
    d.rounded_rectangle((55,55,w-55,h-55),radius=45,fill=(250,252,255),outline=ac,width=8)
    words=(str(topic)+" "+str(heading)).lower()
    if any(k in words for k in ("tibbiyot","tibbiy","biolog","hujayra","anatomi","yurak","shifoxona","medical")):
        mode=5
    elif any(k in words for k in ("iqtisod","moliya","marketing","biznes","bank","savdo","finance","statistika")):
        mode=1
    elif any(k in words for k in ("tarix","qadimgi","siviliz","urush","tarixiy","history")):
        mode=6
    elif any(k in words for k in ("sun'iy intellekt","sun’iy intellekt","texnolog","robot","kompyuter","raqamli","dastur","neyron","it ")) :
        mode=0
    elif any(k in words for k in ("tabiat","ekolog","iqlim","o‘simlik","o'simlik","daraxt","hayvon","nature")):
        mode=4
    elif any(k in words for k in ("ta'lim","ta’lim","maktab","universitet","pedagog","o‘qit","o'qit","education")):
        mode=3
    else:
        mode=digest[3]%5
    if mode==0:
        # network / AI
        pts=[]
        for i in range(9):
            x=180+(i%3)*370+(digest[i]%60); y=150+(i//3)*190+(digest[i+9]%40); pts.append((x,y))
        for i,(x,y) in enumerate(pts):
            for j,(xx,yy) in enumerate(pts):
                if j>i and (i+j)%2==0: d.line((x,y,xx,yy),fill=(*ac,255),width=5)
        for x,y in pts:
            d.ellipse((x-24,y-24,x+24,y+24),fill=ac,outline=(255,255,255),width=6)
    elif mode==1:
        # data bars / infographic
        heights=[140+(digest[i]%300) for i in range(6)]
        for i,v in enumerate(heights):
            x=150+i*135; d.rounded_rectangle((x,h-140-v,x+78,h-140),radius=18,fill=ac if i%2==0 else (72,145,180))
        d.line((120,h-140,w-120,h-140),fill=(70,80,90),width=5)
    elif mode==2:
        # orbit / science
        cx,cy=w//2,h//2
        for r in (95,180,265):
            d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=ac,width=6)
        d.ellipse((cx-45,cy-45,cx+45,cy+45),fill=ac)
        for i in range(8):
            a=i*math.pi/4; x=cx+265*math.cos(a); y=cy+265*math.sin(a)
            d.ellipse((x-18,y-18,x+18,y+18),fill=(55,105,150))
    elif mode==3:
        # editorial cards
        for i in range(3):
            x=130+i*285
            d.rounded_rectangle((x,170,x+220,520),radius=24,fill=(255,255,255),outline=ac,width=6)
            d.ellipse((x+65,210,x+155,300),fill=ac)
            for j in range(3): d.rounded_rectangle((x+35,340+j*42,x+185,355+j*42),radius=7,fill=(160,180,195))
    elif mode==4:
        # abstract landscape / nature
        d.ellipse((730,115,930,315),fill=(242,185,83))
        d.polygon([(70,560),(350,220),(610,560)],fill=(72,135,110))
        d.polygon([(360,560),(710,170),(1050,560)],fill=(49,105,91))
        d.polygon([(280,315),(350,220),(425,315)],fill=(245,248,250))
    elif mode==5:
        # medical cross + cell illustration
        d.rounded_rectangle((145,220,430,505),radius=35,fill=(225,244,248),outline=ac,width=10)
        d.rectangle((255,270,320,455),fill=ac); d.rectangle((195,330,380,395),fill=ac)
        for x,y,r in [(650,260,95),(840,410,65),(800,180,42)]:
            d.ellipse((x-r,y-r,x+r,y+r),fill=(190,225,232),outline=ac,width=8)
            d.ellipse((x-r//3,y-r//3,x+r//3,y+r//3),fill=ac)
    else:
        # history: timeline and classical columns
        d.line((150,440,950,440),fill=ac,width=12)
        for i in range(4):
            x=210+i*220
            d.ellipse((x-25,415,x+25,465),fill=ac)
            d.rectangle((x-50,245,x+50,390),fill=(224,211,185),outline=ac,width=6)
            d.polygon([(x-65,245),(x,190),(x+65,245)],fill=ac)
    # A title strip keeps the asset connected to the actual slide topic.
    title=str(heading or topic)[:58]
    d.rounded_rectangle((90,82,w-90,150),radius=18,fill=(255,255,255))
    d.text((120,99),title,font=_font(30),fill=(35,48,62))
    out=BytesIO(); im.save(out,format="PNG",optimize=True); out.seek(0)
    return out
