from pathlib import Path
from io import BytesIO
from pptx_tools import build_presentation
from slide_images import make_illustration
from slide_themes import THEMES, FRAMES, PALETTES
from slide_export import pptx_to_pdf

OUT=Path("SAMPLES"); OUT.mkdir(exist_ok=True)

def deck(topic, slides, design):
    settings={"design":design,"frame":"thin","font":"Noto Sans"}
    images={i:make_illustration(topic,s["heading"],THEMES[design][2],i) for i,s in enumerate(slides)}
    p=build_presentation(topic,slides,"Student AI",settings,images)
    (OUT/(topic.replace(" ","_")+".pptx")).write_bytes(p.getvalue())
    pdf=pptx_to_pdf(p,topic,slides,settings,images)
    (OUT/(topic.replace(" ","_")+".pdf")).write_bytes(pdf.getvalue())
    return p

KEYNS=[
{"heading":"Modelning asosiy g‘oyasi","bullets":["Qisqa muddatda ishlab chiqarish va bandlikka umumiy talab kuchli ta’sir qiladi.","Muvozanat rejalashtirilgan xarajatlar ishlab chiqarishga tenglashadigan nuqtada shakllanadi.","Model to‘liq bandlik avtomatik ravishda yuz beradi degan qarashni tanqid qiladi."],"layout":"cards"},
{"heading":"Umumiy talab tarkibi","bullets":["Yopiq iqtisodiyotning sodda modelida rejalashtirilgan xarajatlar iste’mol va investitsiyadan tuziladi.","Ochiq iqtisodiyotda davlat xarajatlari va sof eksport ham qo‘shiladi.","Tarkibiy qismlarning o‘zgarishi muvozanat ishlab chiqarishiga ta’sir qiladi."],"layout":"two_col"},
{"heading":"Iste’mol va jamg‘arish","bullets":["Daromad oshganda iste’mol odatda oshadi, ammo iste’mol o‘sishi daromad o‘sishidan kichikroq bo‘lishi mumkin.","Chegaraviy iste’mol moyilligi qo‘shimcha daromadning iste’molga ketadigan qismini bildiradi.","Chegaraviy jamg‘arish moyilligi bilan MPC + MPS = 1 munosabati bajariladi."],"layout":"infographic"},
{"heading":"Investitsiya va foiz stavkasi","bullets":["Foiz stavkasi pasayishi ayrim investitsiya loyihalarining jozibadorligini oshiradi.","Investitsiya qarorlarida kutilayotgan daromad va kapitalning samaradorligi ham muhim.","Shuning uchun investitsiya funksiyasi odatda foiz stavkasiga nisbatan teskari yo‘nalishda tasvirlanadi."],"chart":{"type":"investment_interest","title":"Nazariy teskari bog‘liqlik","labels":["Past","O‘rta","Yuqori","Juda yuqori"],"values":[10,7,4,2]},"layout":"chart"},
{"heading":"Keynschilik xochi","bullets":["45° chiziq har bir daromad darajasida xarajatlar ishlab chiqarishga teng bo‘lishini bildiradi.","Rejalashtirilgan xarajatlar chizig‘i 45° chiziq bilan kesishgan nuqtada muvozanat daromadi aniqlanadi.","Qiymatlar bu yerda nazariy modelni ko‘rsatish uchun berilgan, statistik kuzatuv sifatida talqin qilinmaydi."],"chart":{"type":"keynesian_cross","title":"Keynschilik xochi — nazariy sxema","labels":["Y1","Y2","Y3","Y4","Y5"],"values":[0.2,2,5,7,9]},"layout":"chart"},
{"heading":"Investitsiya multiplikatori","bullets":["Dastlabki xarajat boshqa subyekt uchun daromadga aylanishi sababli qo‘shimcha xarajatlar zanjiri yuzaga keladi.","Sodda modelda multiplikator MPC bilan bog‘liq: k = 1 / (1 − MPC).","Quyidagi ketma-ketlik tushuntiruvchi nazariy misol bo‘lib, real iqtisodiy statistika emas."],"chart":{"type":"multiplier","title":"Nazariy daromad bosqichlari","labels":["1-bosqich","2-bosqich","3-bosqich","4-bosqich"],"values":[100,70,49,34.3]},"layout":"chart"},
{"heading":"Retsession va inflyatsion uzilish","bullets":["Muvozanat daromadi to‘liq bandlik darajasidan past bo‘lsa, retsession uzilish tushunchasi qo‘llanadi.","Umumiy talab ishlab chiqarishning to‘liq bandlik darajasidagi imkoniyatidan oshsa, inflyatsion bosim yuzaga kelishi mumkin.","Fiskal va monetar siyosat talab sharoitlarini o‘zgartirish vositasi sifatida ko‘riladi."],"layout":"comparison"},
{"heading":"Modelning ahamiyati","bullets":["Keyns yondashuvi talab, bandlik va qisqa muddatli ishlab chiqarish o‘rtasidagi bog‘liqlikni tahlil qilishda muhim.","Keyingi makroiqtisodiy maktablar bu g‘oyalarni pul bozori, kutilmalar va boshqa omillar bilan kengaytirdi.","Modelni qo‘llashda uning soddalashtiruvchi farazlari alohida ko‘rsatilishi kerak."],"layout":"text_focus",
"references":["Keynes, J. M. (1936). The General Theory of Employment, Interest and Money. London: Macmillan. https://books.google.com/books/about/The_General_Theory_of_Employment_Interes.html","Richard F. Kahn (2011). The Making of Keynes’ General Theory. Cambridge University Press. https://assets.cambridge.org/97805211/89750/frontmatter/9780521189750_frontmatter.pdf"]}
]
AI=[
{"heading":"Sun’iy intellekt nima?","bullets":["Sun’iy intellekt murakkab vazifalarni bajaradigan hisoblash tizimlari va usullarini o‘rganadi.","Yo‘nalishlarga mashinaviy o‘rganish, tabiiy tilni qayta ishlash, kompyuter ko‘rishi va robototexnika kiradi.","AI tizimi natijasini baholashda maqsad, ma’lumot va xavflar birgalikda ko‘riladi."],"layout":"cards"},
{"heading":"Intellektual agentlar","bullets":["Agent muhitni kuzatadi, mavjud ma’lumotni qayta ishlaydi va maqsadga mos harakat tanlaydi.","Ratsional agent qarorlarni kutilayotgan natija va mavjud ma’lumot asosida tanlaydi.","Agent modeli muhitning kuzatilishi, harakatlar va maqsadlar bilan tavsiflanadi."],"layout":"process"},
{"heading":"Mashinaviy o‘rganish","bullets":["Nazoratli o‘rganishda model belgilangan misollar orqali bog‘lanishni o‘rganadi.","Nazoratsiz o‘rganish ma’lumot ichidagi tuzilmalarni aniqlashga yo‘naltiriladi.","Mustahkamlovchi o‘rganishda agent mukofot signali orqali strategiyasini yaxshilaydi."],"layout":"comparison"},
{"heading":"Neyron tarmoqlar","bullets":["Neyron tarmog‘i qatlamlar va parametrlar orqali kirishdan chiqishga murakkab bog‘lanishni o‘rganadi.","Chuqur o‘rganishda ko‘plab qatlamlar murakkab belgilarni bosqichma-bosqich ajratishi mumkin.","Model sifati ma’lumot, arxitektura, o‘qitish va baholash usullariga bog‘liq."],"layout":"infographic"},
{"heading":"Tabiiy tilni qayta ishlash","bullets":["Til modellari matnni tahlil qilish, yaratish, tarjima va savol-javob kabi vazifalarda ishlatiladi.","Katta til modellari ketma-ketlikdagi bog‘lanishlarni o‘rganish uchun transformer kabi arxitekturalardan foydalanadi.","Natijani tekshirishda faktik xatolar va kontekstga moslik alohida nazorat qilinadi."],"layout":"split_right"},
{"heading":"Kompyuter ko‘rishi va robototexnika","bullets":["Kompyuter ko‘rishi tasvir yoki videodan obyektlar va belgilarni ajratishga yordam beradi.","Robototexnika sezish, rejalashtirish va harakatni bitta tizimda birlashtiradi.","Real muhitda xavfsizlik, kechikish va xatolarga chidamlilik muhim."],"layout":"two_col"},
{"heading":"AI xavfsizligi va etika","bullets":["Model natijalari noto‘g‘ri yoki adolatsiz bo‘lishi mumkin, shuning uchun baholash va nazorat zarur.","Maxfiylik, ma’lumotlardan foydalanish va tushuntiriluvchanlik muhim boshqaruv masalalaridir.","Inson nazorati yuqori xavfli qarorlarda alohida ahamiyatga ega."],"layout":"comparison"},
{"heading":"Xulosa: AI bilan mas’uliyatli ishlash","bullets":["AI samaradorlikni oshirishi mumkin, ammo natijani tekshirish mas’uliyati saqlanib qoladi.","Yaxshi tizim texnologiya, ma’lumot, baholash va foydalanuvchi ehtiyojlarini birlashtiradi.","AI imkoniyatini xavfsizlik va ishonchlilik mezonlari bilan muvozanatlash kerak."],"layout":"text_focus",
"references":["Stuart Russell, Peter Norvig (2021). Artificial Intelligence: A Modern Approach, 4th ed. Pearson. https://www.pearson.com/en-us/pearsonplus/p/9780137505135","NIST (2023). Artificial Intelligence Risk Management Framework (AI RMF 1.0). https://www.nist.gov/itl/ai-risk-management-framework"]}
]
BIO=[
{"heading":"Inson organizmi: tizimlar birligi","bullets":["Organizm hujayralardan tashkil topgan va hujayralar to‘qima, a’zo hamda tizimlarni hosil qiladi.","Organlar o‘zaro bog‘langan holda homeostazni saqlashga xizmat qiladi.","Tuzilma va funksiya o‘rtasidagi bog‘lanish biologiyaning asosiy tamoyillaridan biridir."],"layout":"cards"},
{"heading":"Hujayraning asosiy tuzilishi","bullets":["Hujayra membranasi modda almashinuvi va ichki muhitni ajratishda qatnashadi.","Yadro eukariot hujayralarda genetik axborotni saqlaydi.","Mitoxondriya, ribosoma va endoplazmatik tarmoq kabi tuzilmalar turli vazifalarni bajaradi."],"layout":"two_col"},
{"heading":"To‘qimalardan organlargacha","bullets":["Bir xil yoki o‘xshash vazifani bajaruvchi hujayralar to‘qimalarni hosil qiladi.","Bir nechta to‘qima birlashib organlarni, organlar esa tizimlarni tashkil etadi.","Tizimlar o‘zaro hamkorlik qilib organizmning yaxlit ishlashini ta’minlaydi."],"layout":"process"},
{"heading":"Qon aylanish tizimi","bullets":["Yurak qonni tomirlar orqali haydaydi.","Arteriyalar qonni yurakdan uzoqlashtiradi, venalar esa yurakka qaytaradi.","Kapillyarlar va to‘qimalar o‘rtasida gazlar hamda moddalar almashinuvi sodir bo‘ladi."],"layout":"infographic"},
{"heading":"Nafas olish va gaz almashinuvi","bullets":["Nafas olish tizimi kislorodni qabul qilish va karbonat angidridni chiqarishga xizmat qiladi.","O‘pkadagi alveolalar gaz almashinuvi uchun katta sirt hosil qiladi.","Qon gazlarni organizm bo‘ylab tashishda muhim vositadir."],"layout":"split_right"},
{"heading":"Asab tizimi va boshqaruv","bullets":["Asab tizimi sezgi axborotini qabul qiladi, qayta ishlaydi va javob reaksiyalarini muvofiqlashtiradi.","Markaziy asab tizimi miya va orqa miyadan tashkil topadi.","Periferik asab tizimi markaziy tizimni tana qismlari bilan bog‘laydi."],"layout":"comparison"},
{"heading":"Homeostaz","bullets":["Homeostaz ichki muhitning nisbatan barqaror holatini saqlash jarayonidir.","Harorat, suv-tuz muvozanati va glyukoza darajasi kabi ko‘rsatkichlar boshqaruv mexanizmlariga ega.","Manfiy qayta aloqa homeostazni saqlashda keng tarqalgan mexanizmdir."],"layout":"timeline"},
{"heading":"Xulosa: tuzilma va funksiya","bullets":["Organizm darajasidagi jarayonlar hujayra va to‘qima darajasidagi faoliyatga tayangan.","Tizimlar o‘rtasidagi muvofiqlik hayotiy jarayonlarning davom etishini ta’minlaydi.","Biologik hodisalarni tushuntirishda tuzilma, funksiya va boshqaruvni birgalikda ko‘rish foydali."],"layout":"text_focus",
"references":["Lisa A. Urry, Michael L. Cain, Steven A. Wasserman, Peter V. Minorsky, Rebecca Orr (2020). Campbell Biology, 12th ed. Pearson. https://www.pearson.com/en-us/subject-catalog/p/campbell-biology/P200000007019/9780135856215"]}
]
HIST=[
{"heading":"O‘zbekiston tarixiga kirish","bullets":["O‘zbekiston hududi Markaziy Osiyoning tarixiy va madaniy chorrahalaridan biridir.","Hudud tarixi qadimgi shaharlar, davlatlar, savdo yo‘llari va madaniy almashinuvlar bilan bog‘liq.","Tarixni davrlar va manbalar asosida o‘rganish voqealar o‘rtasidagi aloqani ko‘rishga yordam beradi."],"layout":"cards"},
{"heading":"Qadimgi shaharlar va madaniyatlar","bullets":["Buxoro, Samarqand va Xiva kabi markazlar uzoq tarixiy davrlar davomida muhim madaniy markazlar bo‘lib kelgan.","Arxeologik va yozma manbalar qadimgi jamiyatlarning xo‘jaligi va madaniyatini o‘rganishga xizmat qiladi.","Tarixiy merosni saqlash zamonaviy tadqiqot va ta’lim uchun muhim."],"layout":"timeline"},
{"heading":"Ipak yo‘llari va almashinuv","bullets":["Markaziy Osiyo orqali o‘tgan savdo yo‘llari tovarlar bilan birga bilim va madaniyat almashinuvini ham kuchaytirgan.","Shaharlar savdo, hunarmandchilik va ilmiy faoliyatning muhim markazlariga aylangan.","Yo‘llar va shaharlar tarixi mintaqalararo aloqalarni tushunishga yordam beradi."],"layout":"process"},
{"heading":"Temuriylar davri","bullets":["XIV–XV asrlarda Samarqand yirik siyosiy va madaniy markazlardan biriga aylandi.","Ilm-fan, me’morchilik va san’at rivojida Temuriylar davrining o‘rni katta.","Ulug‘bek rasadxonasi va ilmiy maktablar davrning ilmiy merosini namoyon etadi."],"layout":"infographic"},
{"heading":"Xiva, Buxoro va Qo‘qon siyosiy markazlari","bullets":["XIX asrda hududda bir nechta siyosiy markazlar mavjud bo‘lib, ularning boshqaruv va iqtisodiy tizimlari farqlangan.","Xonliklar tarixi siyosiy tuzilma, savdo va hududiy aloqalar bilan birga o‘rganiladi.","Tarixiy xaritalar hududiy o‘zgarishlarni tushuntirishda foydali manba bo‘la oladi."],"layout":"comparison"},
{"heading":"XX asr va sovet davri","bullets":["XX asrda hudud siyosiy, iqtisodiy va ijtimoiy jihatdan keskin o‘zgarishlarni boshdan kechirdi.","Sanoatlashtirish, kollektivlashtirish va madaniy siyosat jamiyat hayotiga ta’sir ko‘rsatdi.","Davrni baholashda turli manbalar va tarixiy nuqtayi nazarlarni taqqoslash muhim."],"layout":"two_col"},
{"heading":"Mustaqillik davri","bullets":["1991 yilda O‘zbekiston mustaqil davlat sifatida yangi siyosiy davrga kirdi.","Tarixiy merosni asrash, milliy institutlarni rivojlantirish va xalqaro aloqalarni kengaytirish yangi davrning muhim yo‘nalishlaridan bo‘ldi.","Tarixiy markazlar va madaniy obyektlar xalqaro meros tizimlarida ham qayd etilgan."],"layout":"timeline"},
{"heading":"Xulosa: tarixiy meros","bullets":["O‘zbekiston tarixi turli davrlar, xalqlar va madaniy aloqalar kesishmasida shakllangan.","Tarixiy manbalarni tanqidiy o‘qish voqealarni biryoqlama talqin qilishdan saqlaydi.","Madaniy merosni o‘rganish bugungi jamiyatning tarixiy xotirasini mustahkamlaydi."],"layout":"text_focus",
"references":["UNESCO World Heritage Convention. Uzbekistan — World Heritage List and state party information. https://whc.unesco.org/en/statesparties/UZ","UNESCO. Silk Roads Programme. https://en.unesco.org/silkroad/"]}
]

if __name__=="__main__":
    deck("Keynsning tovarlar va xizmatlar bozorida makroiqtisodiy muvozanat modeli",KEYNS,"finance")
    deck("Sun’iy intellekt va zamonaviy texnologiyalar",AI,"technology")
    deck("Inson organizmining tuzilishi",BIO,"medical")
    deck("O‘zbekiston tarixi",HIST,"history")
    print("Generated",len(list(OUT.glob("*.pptx"))),"PPTX files")
