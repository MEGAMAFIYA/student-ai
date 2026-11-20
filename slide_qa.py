"""Static QA for generated PPTX geometry and text density."""
from __future__ import annotations
from pptx import Presentation

def inspect_presentation(data) -> dict:
    prs=Presentation(data)
    sw,sh=prs.slide_width,prs.slide_height
    issues=[]; min_font=999
    for si,slide in enumerate(prs.slides,1):
        for shape in slide.shapes:
            if shape.left<0 or shape.top<0 or shape.left+shape.width>sw or shape.top+shape.height>sh:
                issues.append(f"{si}: element tashqariga chiqdi")
            if getattr(shape,"has_text_frame",False):
                txt=" ".join(p.text for p in shape.text_frame.paragraphs).strip()
                if txt:
                    for p in shape.text_frame.paragraphs:
                        for r in p.runs:
                            if r.font.size:
                                min_font=min(min_font,r.font.size.pt)
    return {"slides":len(prs.slides),"issues":issues,"min_font":None if min_font==999 else min_font,
            "pass":not issues}
