
from pptx import Presentation
from slide_themes import THEMES, FRAMES, PALETTES, FONTS
from pptx_tools import build_presentation
from slide_images import make_illustration
from slide_export import pptx_to_pdf

def _sample():
    slides=[
        {"heading":"Kirish","bullets":["Mavzuning dolzarbligi","Asosiy tushunchalar"],"layout":"split_right"},
        {"heading":"Tahlil","bullets":["Birinchi omil","Ikkinchi omil"],"layout":"chart","chart":{"labels":["A","B","C"],"values":[3,7,5]}},
        {"heading":"Natijalar","bullets":["Asosiy natija","Tavsiyalar"],"layout":"table","table":[["Mezon","Natija"],["A","Yaxshi"]]},
    ]
    images={i:make_illustration("Sinov mavzusi",s["heading"],"#1677C8",i) for i,s in enumerate(slides)}
    return slides,images

def test_all_twenty_themes_create_valid_pptx():
    slides,images=_sample()
    assert len(THEMES)==20
    for theme in THEMES:
        result=build_presentation("Sinov mavzusi",slides,"Student AI",{"design":theme,"frame":"thin","font":"DejaVu Sans"},images)
        prs=Presentation(result)
        assert len(prs.slides)==len(slides)+2

def test_all_frames_and_palettes_are_accepted():
    slides,images=_sample()
    for frame in FRAMES:
        for palette in PALETTES:
            result=build_presentation("O‘zbekcha O‘ G‘",slides,"Student AI",{"design":"academic","frame":frame,"palette":palette,"font":"Noto Sans"},images)
            assert len(Presentation(result).slides)==5

def test_pdf_export_returns_valid_pdf():
    slides,images=_sample()
    pptx=build_presentation("Sinov",slides,"Student AI",{"design":"modern_blue","frame":"gold","font":"Arial"},images)
    pdf=pptx_to_pdf(pptx,"Sinov",slides,{"design":"modern_blue","frame":"gold"},images)
    assert pdf.getvalue().startswith(b"%PDF")
