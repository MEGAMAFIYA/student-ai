from pptx_tools import build_presentation
from slide_images import make_illustration
from slide_qa import inspect_presentation

def test_generated_deck_has_no_out_of_bounds_shapes():
    slides=[{"heading":"Kirish","bullets":["Birinchi fikr","Ikkinchi fikr"],"layout":"cards"},
            {"heading":"Diagramma","bullets":["Nazariy misol"],"layout":"chart",
             "chart":{"type":"line","labels":["A","B","C"],"values":[1,2,3]}}]
    images={0:make_illustration("Sinov","Kirish"),1:make_illustration("Sinov","Diagramma")}
    p=build_presentation("Sinov",slides,"Student AI",{"design":"technology","frame":"thin","font":"Noto Sans"},images)
    q=inspect_presentation(p)
    assert q["pass"], q["issues"]
    assert q["slides"]==4
    assert q["min_font"] >= 8
