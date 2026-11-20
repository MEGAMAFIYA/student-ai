"""Offline, deterministic, topic-aware visual assets. Images contain no generated text."""
from io import BytesIO
import hashlib, math, re
from PIL import Image, ImageDraw, ImageFont

def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"):
        try: return ImageFont.truetype(path,size)
        except Exception: pass
    return ImageFont.load_default()

def _accent(accent):
    try: return tuple(int(accent.lstrip("#")[i:i+2],16) for i in (0,2,4))
    except Exception: return (40,120,190)

def make_illustration(topic,heading,accent="#3B82F6",seed=0,size=(1100,700)):
    digest=hashlib.sha256((str(topic)+str(heading)+str(seed)).encode()).digest()
    ac=_accent(accent); w,h=size
    im=Image.new("RGB",size,(246,249,252)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((35,35,w-35,h-35),radius=42,fill=(252,253,255),outline=ac,width=7)
    words=(str(topic)+" "+str(heading)).lower().replace("’","'")
    # Semantic visual families; no random generic image is used when a clear family is known.
    if any(k in words for k in ("keyns","iqtisod","moliya","bozor","talab","taklif","investits","foiz","multiplikator","makro")):
        # Axes + demand/supply-like curves + money blocks.
        x0,y0=145,570; x1,y1=970,130
        d.line((x0,y0,x1,y0),fill=(65,75,85),width=6); d.line((x0,y0,x0,y1),fill=(65,75,85),width=6)
        pts=[]
        for i in range(8):
            x=x0+70+i*105; y=y0-35-i*47+(digest[i]%18)
            pts.append((x,y))
        for a,b in zip(pts,pts[1:]): d.line((a,b),fill=ac,width=8)
        pts2=[]
        for i in range(8):
            x=x0+70+i*105; y=y1+45+i*58-(digest[i+8]%18)
            pts2.append((x,y))
        for a,b in zip(pts2,pts2[1:]): d.line((a,b),fill=(110,150,175),width=8)
        for x,y in (pts[3],pts2[4]): d.ellipse((x-16,y-16,x+16,y+16),fill=ac)
        for i in range(4): d.rounded_rectangle((720+i*60,185-i*15,765+i*60,255-i*15),radius=8,fill=ac)
    elif any(k in words for k in ("sun'iy intellekt","sun’iy intellekt","texnolog","robot","kompyuter","raqamli","neyron")):
        pts=[(180+(i%4)*230+(digest[i]%30),170+(i//4)*260+(digest[i+8]%30)) for i in range(8)]
        for i,(x,y) in enumerate(pts):
            for j,(xx,yy) in enumerate(pts):
                if j>i and (i+j)%3==0: d.line((x,y,xx,yy),fill=ac,width=5)
            d.ellipse((x-28,y-28,x+28,y+28),fill=ac,outline=(255,255,255),width=6)
    elif any(k in words for k in ("qon aylanish","qon","yurak")):
        cx,cy=330,350
        d.ellipse((cx-115,cy-140,cx+115,cy+140),fill=(248,232,235),outline=ac,width=9)
        d.polygon([(cx,cy-20),(cx+90,cy+55),(cx,cy+145),(cx-90,cy+55)],fill=ac)
        for i in range(4):
            d.arc((560+i*75,170+i*8,760+i*75,540-i*8),20,300,fill=ac,width=10)
    elif any(k in words for k in ("nafas","o'pka","o‘pka","gaz almashinuvi")):
        for cx in (320,560):
            d.ellipse((cx-105,210,cx+105,520),fill=(232,247,249),outline=ac,width=9)
        d.line((440,150,440,260),fill=ac,width=15)
        d.line((440,260,330,330),fill=ac,width=12); d.line((440,260,550,330),fill=ac,width=12)
        for x,y in [(700,230),(800,300),(720,410),(830,450)]:
            d.ellipse((x-28,y-28,x+28,y+28),fill=ac)
    elif any(k in words for k in ("asab","boshqaruv")):
        cx,cy=390,350
        d.ellipse((cx-145,cy-130,cx+145,cy+130),fill=(238,235,249),outline=ac,width=9)
        for i in range(8):
            a=i*math.pi/4; x=cx+100*math.cos(a); y=cy+85*math.sin(a)
            d.line((cx,cy,x,y),fill=ac,width=8); d.ellipse((x-18,y-18,x+18,y+18),fill=ac)
        d.line((700,180,700,520),fill=ac,width=10)
        for i in range(5): d.ellipse((680,190+i*70,720,230+i*70),fill=ac)
    elif any(k in words for k in ("biolog","hujayra","anatomi","organizm","tibbiyot")):
        cx,cy=360,350
        d.ellipse((cx-150,cy-150,cx+150,cy+150),fill=(232,247,249),outline=ac,width=10)
        for ang in range(0,360,45):
            x=cx+105*math.cos(math.radians(ang)); y=cy+105*math.sin(math.radians(ang))
            d.ellipse((x-22,y-22,x+22,y+22),fill=ac)
        d.rounded_rectangle((650,180,900,520),radius=40,fill=(242,247,250),outline=ac,width=8)
        d.line((710,350,840,350),fill=ac,width=18); d.line((775,285,775,415),fill=ac,width=18)
    elif any(k in words for k in ("tarix","qadimgi","siviliz","urush","tarixiy","o'zbekiston","o‘zbekiston")):
        y=500; d.line((120,y,980,y),fill=ac,width=12)
        for i in range(5):
            x=180+i*190; d.ellipse((x-25,y-25,x+25,y+25),fill=ac)
            d.rectangle((x-48,250-(i%2)*35,x+48,y-45),fill=(220,208,187),outline=ac,width=6)
            d.polygon([(x-62,250-(i%2)*35),(x,195-(i%2)*35),(x+62,250-(i%2)*35)],fill=ac)
    elif any(k in words for k in ("ta'lim","ta’lim","maktab","universitet","pedagog","o'qit","o‘qit")):
        for i in range(3):
            x=150+i*285
            d.rounded_rectangle((x,180,x+220,500),radius=28,fill=(255,255,255),outline=ac,width=7)
            d.ellipse((x+65,215,x+155,305),fill=ac)
            for j in range(3): d.rounded_rectangle((x+35,350+j*40,x+185,365+j*40),radius=7,fill=(170,185,198))
    elif any(k in words for k in ("tabiat","ekolog","iqlim","o'simlik","o‘simlik","daraxt","hayvon")):
        d.ellipse((720,100,920,300),fill=(241,191,80))
        d.polygon([(70,560),(350,190),(610,560)],fill=(70,135,105))
        d.polygon([(360,560),(710,160),(1050,560)],fill=(50,105,88))
    else:
        # Generic but still semantic: concept nodes rather than a stock-looking random chart.
        pts=[(180,180),(550,120),(880,200),(300,500),(720,500)]
        for i,(x,y) in enumerate(pts):
            for j,(xx,yy) in enumerate(pts):
                if j>i and (i+j)%2==0: d.line((x,y,xx,yy),fill=ac,width=5)
            d.ellipse((x-40,y-40,x+40,y+40),fill=ac)
    out=BytesIO(); im.save(out,format="PNG",optimize=True); out.seek(0); return out
