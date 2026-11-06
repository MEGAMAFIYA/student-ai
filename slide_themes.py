
"""20 professional slide themes and accessible color palettes."""
from pptx.dml.color import RGBColor

THEMES = {
"modern_blue": ("Modern Blue","#102A43","#1677C8","#EAF4FF","modern"),
"premium_gold": ("Premium Gold","#171717","#C9A227","#F7F0DE","gold"),
"academic": ("Academic","#F7F4EC","#24476B","#263238","academic"),
"minimalist": ("Minimalist","#FFFFFF","#222222","#444444","minimal"),
"corporate": ("Corporate","#F2F5F8","#17365D","#263746","corporate"),
"dark_premium": ("Dark Premium","#10131B","#7C9CFF","#F4F6FC","dark"),
"technology": ("Technology","#071B2B","#00D4FF","#E5FAFF","tech"),
"creative": ("Creative","#FFF5F2","#E84A8A","#34233D","creative"),
"nature": ("Nature","#F1F7EF","#347A50","#263B2E","nature"),
"medical": ("Medical","#F3FAFC","#168C9B","#20353A","medical"),
"finance": ("Finance","#F4F6F3","#1F6B50","#24352D","finance"),
"education": ("Education","#FFF9E9","#D17A22","#3B3023","education"),
"history": ("History","#F1E9D9","#805B36","#30271F","history"),
"science": ("Science","#F3F1FA","#6046A5","#302A40","science"),
"luxury": ("Luxury","#090909","#D4AF37","#F5EBD0","luxury"),
"gradient": ("Gradient","#172554","#8B5CF6","#F5F3FF","gradient"),
"glass": ("Glass","#EAF2F8","#397AA5","#1D3444","glass"),
"elegant": ("Elegant","#FAF7F2","#9B6B73","#392E32","elegant"),
"newspaper": ("Newspaper","#F4F0E7","#222222","#252525","news"),
"infographic": ("Infographic","#F4F7FB","#F07836","#233247","info"),
}
PALETTES = {
"blue_white": ("Ko‘k va oq","#FFFFFF","#1D4E89","#243447"),
"black_gold": ("Qora va oltin","#171717","#D5AD42","#F4E8C8"),
"green_white": ("Yashil va oq","#FFFFFF","#286044","#263B2E"),
"purple_pink": ("Binafsha va pushti","#F7EFF8","#704080","#3B2D42"),
"red_white": ("Qizil va oq","#FFFFFF","#A52A3A","#392A2D"),
"gray_blue": ("Kulrang va ko‘k","#EEF2F5","#345B7E","#263746"),
"forest_gold": ("To‘q yashil va oltin","#123B32","#D5B45B","#F1E9D5"),
"white_black": ("Oq va qora","#FFFFFF","#202020","#252525"),
"blue_gradient": ("Moviy gradient","#164E83","#38BDF8","#FFFFFF"),
}

FRAMES = {
"none":"Ramkasiz","thin":"Yupqa chiziqli","double":"Ikki qavatli",
"gold":"Oltin rangli","silver":"Kumush rangli","corners":"Burchak naqshlari",
"geometric":"Geometrik naqshlar","classic":"Klassik dekorativ",
"minimal":"Zamonaviy minimal","premium":"Premium bezakli",
}
FONTS = ["Arial","Aptos","Calibri","Times New Roman","Georgia","Verdana","DejaVu Sans","Noto Sans"]
