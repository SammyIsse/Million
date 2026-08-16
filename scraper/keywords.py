import os
import re

NON_FOOD_KEYWORDS = {
    # Hygiejne & pleje
    # Bemærk: bare 'creme' undgås bevidst - rammer fødevarer som
    # "cremefraiche"/"flødecreme". Kun specifikke kosmetik-cremer blokeres.
    'indlæg', 'batteri', 'shampoo', 'balsam', 'lotion', 'bleer',
    'hårpleje', 'hårfarve', 'hårspray', 'hårvoks', 'hårgelé', 'hårprodukter',
    'ansigtscreme', 'håndcreme', 'fodcreme', 'bodycreme', 'natcreme',
    'dagcreme', 'øjencreme', 'hudcreme', 'fugtighedscreme', 'børnecreme',
    'zinkcreme', 'zinksalve', 'hælecreme',
    'babypudder', 'babypulver', 'badeolie', 'babyolie',
    'bleposer', 'vaskeserviet', 'vådserviet', 'skumvaskeklud', 'sutteflaske',
    'tandpasta', 'tandbørste', 'håndsæbe', 'shower gel', 'showergel', 'deodorant',
    'deospray', 'bind', 'tampon', 'babypads', 'babybleer',
    'solcreme', 'sollotion', 'solspray', 'solfaktor', 'hudpleje',
    'mascara', 'neglelak', 'parfume', 'makeupfjerner', 'brusegel',
    'mundpleje', 'mundskyl', 'tandblegning', 'tandtrådsbøjle', 'tandkrus',
    'colgate', 'zendium', 'oral-b', 'listerine', 'signal tandpasta',
    'sæbedispenser', 'sæbeholder', 'sæbepumpe',
    # K-beauty & skønhedsmærker
    'serum', 'cosrx', 'biodance', 'medicube', 'anua', 'cerave', 'la roche',
    'peptide', 'niacinamide', 'retinol', 'hyaluronsyre', 'hyaluron',
    'dufte til', 'parfumeset',
    # Pleje-mærker (shampoo/balsam/deo)
    'head & shoulders', 'taft', 'nivea', 'garnier', 'elvive', 'pantene',
    'syoss', 'sanex', 'rexona', 'schwarzkopf',
    # Kæledyr
    'hundemad', 'hundefoder', 'kattefoder', 'kattemad', 'hundesnack', 'kattegrus', 'pedigree',
    # 'felix' alene blokerede hele Orklas fødevaremærke (agurkesalat, rødkål,
    # ketchup). Kattemaden hedder altid Felix + serienavn, så vi rammer den i
    # stedet på serienavnene - og på de generelle katte-ord ovenfor/nedenfor.
    'whiskas', 'felix party', 'felix sensations', 'felix kattemad',
    'felix soup', 'felix crispies', 'felix as good as', 'felix doubly',
    'royal canin', 'purina', 'dreamies', 'friskies',
    'kattesand', 'kattebakke', 'hundelegetøj', 'kattemøbel',
    'dyremad', 'tørfoder', 'vådfoder', 'hundepaté', 'kattepaté',
    'hundeposer', 'kattesnacks', 'kattepouch', 'hundetygge', 'kattesticks',
    'tyggestænger', 'tyggestrips', 'tyggeben', 'snackstang', 'snackstænger',
    'godbidder', 'raakraft', 'killing m.',
    # Rengøring
    'opvaskemiddel', 'vaskemiddel', 'skyllemiddel', 'opvasketabs', 'vaskekapsler',
    'vaske-middel', 'toiletrengøring', 'rengøring', 'bref', 'domestos', 'harpic',
    'håndopvask', 'tabs', 'scrub daddy', 'vileda', 'skuresvampe',
    'tøjvask', 'tøjrens', 'pletfjerner',
    'maskinopvask', 'neophos', 'bamseline', 'fairy opvask',
    # Rengøring/vask-mærker
    'biotex', 'wonderwash', 'vanish', 'dun-let', 'lenor', 'art of the refill',
    'opvaskebørste', 'toiletbørste', 'wc-børste',
    # Tobak & nikotin
    'tobak', 'cigaret', 'cigarillo', 'snus', 'nikotin', 'tændstik',
    'lighter', 'fyrstikker', 'nicotinell', 'nikotinplaster', 'nikotintyggegummi',
    'nikoret', 'niquitin',
    # Papirvarer & engangsservice
    'toiletpapir', 'køkkenrulle', 'køkken rulle', 'bagepapir', 'kleenex',
    'servietter', 'papkrus', 'paptallerken', 'engangsservice', 'lambi',
    # Planter & blomster
    # 'plante'/'blomst' må kun ramme ordenden (se _match_re nedenfor), ellers
    # ryger hele det plantebaserede sortiment og blomsterhonningen med.
    # "Potteplante" fanges stadig, fordi keywordet ikke kræver ordstart.
    'plante', 'planter', 'planteskole', 'potte', 'potteskjuler',
    'blomst', 'blomster', 'blomsterbuket', 'blomsterløg',
    'roser', 'tulipaner', 'orkidé', 'krysantemum', 'gødning',
    'yucca', 'cycas', 'monstera', 'dracaena', 'agave', 'kaktus', 'bambuspalme',
    'kalanchoe', 'sukkulent', 'hedera', 'ficus', 'begonia', 'petunia',
    'pottejord', 'plantejord', 'havejord', 'blomsterjord', 'pottemuld', 'spagnum',
    # Lys & lysdekorationer
    'fyrfadslys', 'stearinlys', 'kronelys', 'bloklys', 'levende lys',
    'duftlys', 'citronellalys', 'citronella lys', 'citronella', 'stagelys',
    'betonstage', 'lys i glas', 'havefakkel', 'fakkel',
    # Maskiner & elektronik
    'kaffemaskine', 'kaffemaskiner', 'espressomaskine', 'kapselmaskine',
    'toaster', 'brødrister', 'køleboks', 'køletaske', 'terrassevarmer',
    'elkedel', 'airfryer', 'robotplæneklipper', 'støvsuger', 'strygerobot',
    'kogeplade', 'induktionskogeplade', 'gaskomfur', 'el-komfur',
    'støvsugerpose', 'højtaler', 'mobiltilbehør', 'ismaskine', 'insektstik',
    'vaskemaskine', 'opvaskemaskine', 'tørretumbler',
    'køleskab', 'fryseskab', 'køle-fryseskab',
    'oneblade', 'barbermaskine', 'epilator', 'hårtørrer', 'glattejern',
    'headset', 'earbuds', 'høretelefoner',
    # Køkkengrej & husholdning
    'stegepande', 'tørrestativ', 'termokande', 'opbevaring', 'kurv',
    'tramontina', 'smartstore', 'husholdningsprodukter',
    'santoku', 'kniv', 'bestik', 'skærebræt', 'skræller', 'perleboks',
    'duge', 'bordløber', 'dækkeserviet',
    'husholdningsmarked', 'palmemarked', 'fritvalgsmarked', 'sæsonmarked',
    # Køkkengrej (specifikke produkter)
    'glaslåg', 'rivejern', 'kageform', 'keramikredskaber',
    'drikkekop', 'tallerken', 'tallerkner', 'plastservice', 'sprinklervæske', 'motorolie',
    'frostvæske', 'vandkande', 'vandpistol',
    # Tøj, sko & sport
    'sneakers', 'nike', 'hummel', 'friends', 'latz', 'jackpot', 't-shirt',
    'solbriller', 'sommerhat', 'gummisko', 'strandtaske', 'leggings',
    'badebukser', 'badetøj', 'badedragt',
    'bukser', 'jeans', 'shorts', 'trøje', 'jakke', 'frakke', 'anorak',
    'bluse', 'skjorte', 'underbukser', 'undertøj', 'trusser', 'sokker', 'strømpe',
    'strømper', 'tørklæde', 'bælte', 'handske', 'bøllehat', 'stråhat',
    'kjole', 'nederdel', 'cardigan', 'sandaler', 'støvler', 'stiletter',
    'shopper', 'indkøbstaske',
    'slip-on', 'slip on sko', 'lyssko', 'legesko',
    'libresse', 'tena', 'libero',
    'solpleje', 'solbeskyttelse', 'after sun', 'aftersun',
    # Udendørs & fritid
    'solseng', 'parasol', 'badeklæde', 'fuglebad', 'fiskegrej', 'høreværn',
    'badevinger', 'badedyr', 'strandbold', 'kuglepistol', 'fodbold',
    # Soveværelse & tekstiler
    'sengetøj', 'sengetæppe', 'sengesæt', 'gavlpude', 'siddehynde', 'hynde', 'dørmåtte',
    'sommerdyne', 'vinterdyne', 'topmadras', 'sjippetov', 'airtrack',
    # Bad & tekstil
    'badeforhæng', 'badekåbe', 'bademåtte', 'håndklæde', 'vaskeklud',
    'morgenkåbe', 'natdragt', 'natkjole', 'badekar',
    # Møbler & have
    'havestol', 'spisebordsstol', 'lænestol', 'liggestol', 'klapstol',
    'loungestol', 'hvilestol', 'kontorstol', 'barstol', 'festivalsstol', 'festivalstol',
    'gyngestol', 'havebord', 'sofabord', 'spisebord', 'havemøbel', 'havemøbler',
    'gasgrill', 'kulgrill', 'el-grill', 'pizzaovn', 'grillvogn', 'engangsgrill',
    'vattæppe', 'uldtæppe', 'fleecetæppe', 'strikketæppe',
    'affaldsspand', 'rengøringsspand', 'skraldespand', 'spand med udvrider', 'havelys',
    'udvrider', 'graveredskaber', 'sæbeboblesværd', 'sandlegetøj',
    'havenisse', 'havefigur', 'havepynt', 'sommerpynt',
    'krukke', 'trolley', 'telt', 'slipper', 'hjemmesko', 'kasket', 'uneflex',
    # Boligindretning
    'biopejs', 'naturfyldspude', 'lampeskærm', 'vase', 'skammel',
    # Gavekort & diverse ikke-mad
    'gavekort', 'gift card',
    # Legetøj & hobby
    # 'jumbo' alene blokerede jumbo rejer, jumbo risvafler osv. - spilmærket
    # står altid sammen med spiltypen.
    'hot wheels', 'legetøj', 'kridt', 'strandkridt', 'gadekridt',
    'jumbo puslespil', 'jumbo spil', 'jumbo bamse', 'frisbee',
    'tøjbamse', 'plysbamse', 'bamsedyr',
    'nissehave', 'sommernissehave', 'tuscher', 'twinmarker',
    'kongespil', 'brætspil', 'kortspil', 'puslespil', 'terningespil',
    'samlealbum', 'klistermærke',
    'lego', 'playmobil', 'dukkehus', 'legesæt', 'squishy', 'slime', 'fidget',
    'lekaform', 'plysfigur', 'minifigurer', 'walkie', 'reparationssæt',
    # Kunst & håndværk
    'akrylmaling', 'malebog', 'malesæt', 'malemåtte', 'hobbybog',
    'selvhærdende', 'mal-selv', 'krea', 'hulten', 'dual markers', 'børstebog',
    # Bøger & medier
    'børnebog', 'lydbog', 'aktivitetsbog', 'tegneserie', 'notesbog', 'bogmarked',
    # Stationery
    'kuglepen', 'blyant', 'viskelæder',
    # Elektronik (nye)
    'doro', 'godt papir',
    # Kosttilskud & sundhed
    'vitaminer', 'livol', 'gerimax', 'kosttilskud', 'proteinpulver',
    'whey protein', 'kreatin', 'collagen', 'omega-3 kapsler',
    # Forbrugerelektronik (fx Føtex sælger tv, telefoner og tilbehør)
    'samsung', 'iphone', 'ipad', 'macbook', 'airpods', 'huawei', 'xiaomi',
    'oneplus', 'hisense', 'prosonic', 'tp-link', 'tcl', 'zte',
    'philips', 'denver tablet', 'denver 8', 'denver 10', 'lenovo', 'acer', 'asus tablet',
    'tablet til børn', 'børnetablet', 'barnestablet',
    'sandisk', 'flashdrive', 'microsd', 'usb-stick',
    'lg 3', 'lg 4', 'lg 5', 'lg 6', 'lg 7', 'lg oled', 'lg nanocell',
    'smart tv', 'fjernsyn', 'soundbar', 'høretelefon',
    'hovedtelefoner', 'øretelefoner', 'mobiltelefon', 'smartphone',
    'powerbank', 'playstation', 'nintendo', 'smartwatch', 'højttaler',
    'printer', 'router', 'kamera',
    'ps5', 'ps4', 'xbox', 'switch', 'gaming',
}

FOOD_KEYWORDS = {
    # Mejeri
    'mælk', 'smør', 'ost', 'yoghurt', 'ymer', 'skyr', 'fløde', 'cremefraiche',
    'kvark', 'hytteost', 'mozzarella', 'brie', 'camembert', 'cheddar',
    # Kød & fisk
    'kød', 'oksekød', 'svinekød', 'lammekød', 'kylling', 'kalkun', 'and',
    'fisk', 'laks', 'torsk', 'rødspætte', 'sild', 'makrel', 'tun', 'rejer',
    'pålæg', 'skinke', 'salami', 'leverpostej', 'spegepølse', 'rullepølse',
    'hakket', 'filet', 'bøf', 'schnitzel', 'koteletter', 'ribben', 'pølse',
    # Frugt & grønt
    'frugt', 'grønt', 'grøntsager', 'æble', 'pære', 'banan', 'appelsin',
    'citron', 'lime', 'mango', 'ananas', 'jordbær', 'hindbær', 'blåbær',
    'vindrue', 'kirsebær', 'avocado', 'tomat', 'agurk', 'gulerod', 'løg',
    'kartoffel', 'broccoli', 'blomkål', 'spinat', 'salat', 'peberfrugt',
    'svampe', 'majs', 'ærter', 'bønner', 'linser', 'selleri', 'purre',
    # Brød & kager
    'brød', 'rugbrød', 'franskbrød', 'boller', 'kage', 'wienerbrød',
    'croissant', 'bagel', 'focaccia', 'ciabatta', 'knækbrød', 'rundstykker',
    # Drikkevarer
    'juice', 'appelsinjuice', 'vand', 'cola', 'sodavand', 'saft', 'limonade',
    'øl', 'vin', 'rødvin', 'hvidvin', 'rosé', 'champagne', 'cider',
    'kaffe', 'te', 'kakao', 'kaffekapsler', 'mælkedrink', 'smoothie', 'energidrik',
    # Morgenmad & cerealier
    'havregryn', 'cornflakes', 'müsli', 'granola', 'morgenmad', 'grød',
    # Kolonial & tørvarer
    'pasta', 'spaghetti', 'penne', 'ris', 'mel', 'sukker', 'salt', 'peber',
    'olie', 'olivenolie', 'rapsolie', 'eddike', 'sauce', 'ketchup', 'sennep',
    'mayonnaise', 'dressing', 'bouillon', 'suppe', 'konserves',
    'honning', 'marmelade', 'syltetøj', 'nutella', 'peanutbutter',
    'krydderier', 'urter', 'karry', 'paprika', 'oregano',
    # Frost & færdigretter
    'frosne', 'frossen', 'frost', 'is ', 'flødeis', 'sorbet',
    'pizza', 'lasagne', 'færdigret',
    # Slik & snacks
    'chokolade', 'slik', 'lakrids', 'vingummi', 'karamel', 'drops',
    'chips', 'kiks', 'popcorn', 'nødder', 'mandler', 'cashewnødder',
    'jordnødder', 'pistacienødder', 'snack', 'proteinbar',
    # Æg & plantebaseret
    'æg', 'tofu', 'hummus', 'dips', 'guacamole',
}

# Extra keywords loaded at runtime from data/ text files (user-curated via review_ai_decisions.py)
_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
_extra_blocked_file = os.path.join(_data_dir, 'extra_blocked_keywords.txt')
_extra_food_file = os.path.join(_data_dir, 'extra_food_keywords.txt')


def _load_extra(path: str) -> set:
    try:
        with open(path, encoding='utf-8') as f:
            return {line.strip().lower() for line in f if line.strip() and not line.startswith('#')}
    except FileNotFoundError:
        return set()


NON_FOOD_KEYWORDS |= _load_extra(_extra_blocked_file)
FOOD_KEYWORDS |= _load_extra(_extra_food_file)

# Ord der kun er ikke-mad når de optræder som hele ord (ikke som del af et større ord).
# Eksempel: 'bh' blokerer "Bh" (bystenolder) men må IKKE blokere "Bhaji".
#           'ovn' blokerer "Ovn" (køkkenudstyr) men må IKKE blokere "Ovnklar kylling".
#           'spand' blokerer "Spand 10 L" men må IKKE blokere "Spandauer boller".
NON_FOOD_EXACT_WORDS = {
    'bh', 'ovn', 'spand', 'pande', 'kurv',
}

# ── Ordgrænse-matching ────────────────────────────────────────────────────────
# Ren substring-matching var årsagen til hele klassen af fejlklassifikationer:
# 'plante' spærrede Plantefars, 'blomst' spærrede blomsterhonning, 'bind'
# spærrede bindsalat - og på hvidlisten slap Toaster ('te'), Køleboks ('øl') og
# Frisbee ('ris') igennem som mad.
#
# De to lister har bevidst FORSKELLIG grænse-strenghed:
#
#   NON_FOOD: kun grænse til HØJRE. Danske sammensætninger sætter kernen sidst
#     ("babyshampoo", "børnetandpasta", "hundeshampoo"), så et krav om ordstart
#     ville lukke store dele af non-food-sortimentet ind. Til gengæld skal
#     keywordet slutte hvor ordet slutter, og det er præcis den side fejlene
#     sad på. Bonus: "Potteplante" fanges stadig af 'plante'.
#
#   FOOD: grænse i BEGGE ender. Et hvidliste-hit springer cachen over, så et
#     falsk hit er dyrere end et manglende - og et manglende hit koster intet,
#     fordi fail-safe i forvejen er "inkludér".
#
# Danske bøjningsendelser tillades efter keywordet, så 'æble' stadig rammer
# "æbler" og 'plante' stadig rammer "planter".
_LETTER = r'[^\W\d_]'
_INFLECTION = r'(?:erne|ene|er|en|et|ne|e|r|s)?'


def _compile_keywords(keywords: set, *, anchor_left: bool) -> re.Pattern:
    parts: list[str] = sorted(
        (re.escape(k.strip()) for k in keywords if k.strip()),
        key=len,
        reverse=True,
    )
    left = rf'(?<!{_LETTER})' if anchor_left else ''
    return re.compile(
        rf'{left}(?:{"|".join(parts)}){_INFLECTION}(?!{_LETTER})',
        re.IGNORECASE,
    )


_NON_FOOD_RE = _compile_keywords(NON_FOOD_KEYWORDS, anchor_left=False)
_FOOD_RE = _compile_keywords(FOOD_KEYWORDS, anchor_left=True)
_EXACT_SPLIT_RE = re.compile(r'[^a-zæøå]+')


# Kendte falske positiver fra anchor_left=False (tillader et vilkårligt
# præfiks foran nøgleordet, se _compile_keywords) - opdaget ved test mod
# den reelle produkt-cache: 'blomst' (skåret blomst) rammer "hyldeblomst"
# (en af de mest almindelige danske saftsmage), 'doro' (mobilmærket Doro)
# rammer "passata di pomodoro"/"pomodoro" (italiensk tomat), 'acer'
# (laptop-mærket) rammer vinbrandet "Piacere". Tjekkes FØR hovedregexen,
# samme mønster som LU Prince-kiks-undtagelsen i app_support.py. Se
# matchmotor-revisionen 2026-08-16, fund H2.
_NON_FOOD_FALSE_POSITIVE_RE = re.compile(
    r'hyldeblomst|passata|pomodoro|piacere', re.IGNORECASE)


def matches_non_food(text: str) -> bool:
    """True hvis teksten indeholder et sortliste-ord (afsluttet ved ordgrænse)."""
    t = (text or '').lower()
    if _NON_FOOD_FALSE_POSITIVE_RE.search(t):
        return False
    if _NON_FOOD_RE.search(t):
        return True
    return bool(set(_EXACT_SPLIT_RE.split(t)) & NON_FOOD_EXACT_WORDS)


def matches_food(text: str) -> bool:
    """True hvis teksten indeholder et hvidliste-ord som helt ord."""
    return bool(_FOOD_RE.search((text or '').lower()))


def is_non_food(heading: str) -> bool:
    """Returnerer True hvis overskriften er ikke-mad."""
    return matches_non_food(heading)
