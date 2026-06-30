import os

NON_FOOD_KEYWORDS = {
    # Hygiejne & pleje
    # Bemærk: bare 'creme' undgås bevidst — rammer fødevarer som
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
    # Kæledyr
    'hundemad', 'kattefoder', 'kattemad', 'hundesnack', 'kattegrus', 'pedigree',
    'whiskas', 'felix', 'royal canin', 'purina', 'dreamies', 'friskies',
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
    # Tobak & nikotin
    'tobak', 'cigaret', 'cigarillo', 'snus', 'nikotin', 'tændstik',
    'lighter', 'fyrstikker', 'nicotinell', 'nikotinplaster', 'nikotintyggegummi',
    'nikoret', 'niquitin',
    # Papirvarer & engangsservice
    'toiletpapir', 'køkkenrulle', 'køkken rulle', 'bagepapir', 'kleenex',
    'servietter', 'papkrus', 'paptallerken', 'engangsservice', 'lambi',
    # Planter & blomster
    'plante', 'planter', 'potte', 'potteskjuler', 'blomst', 'blomster',
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
    # Møbler & have
    'havestol', 'spisebordsstol', 'lænestol', 'liggestol', 'klapstol',
    'loungestol', 'hvilestol', 'kontorstol', 'barstol', 'festivalsstol', 'festivalstol',
    'gyngestol', 'havebord', 'sofabord', 'spisebord', 'havemøbel', 'havemøbler',
    'gasgrill', 'kulgrill', 'el-grill', 'pizzaovn', 'grillvogn',
    'vattæppe', 'uldtæppe', 'fleecetæppe', 'strikketæppe',
    'affaldsspand', 'rengøringsspand', 'skraldespand', 'spand med udvrider', 'havelys',
    'udvrider', 'graveredskaber', 'sæbeboblesværd', 'sandlegetøj',
    'havenisse', 'havefigur', 'havepynt', 'sommerpynt',
    'krukke', 'trolley', 'telt', 'slipper', 'hjemmesko', 'kasket', 'uneflex',
    # Gavekort & diverse ikke-mad
    'gavekort', 'gift card',
    # Legetøj & hobby
    'hot wheels', 'legetøj', 'kridt', 'strandkridt', 'gadekridt', 'jumbo',
    'nissehave', 'sommernissehave', 'tuscher', 'twinmarker',
    'kongespil', 'brætspil', 'kortspil', 'puslespil', 'terningespil',
    'samlealbum', 'klistermærker',
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


def is_non_food(heading: str) -> bool:
    """Returnerer True hvis overskriften er ikke-mad.

    Tjekker både substring-keywords og hel-ords-keywords så korte ord som
    'bh' og 'ovn' ikke rammer fødevarer der starter med disse bogstavsekvenser.
    """
    import re
    h = heading.lower()
    if any(kw in h for kw in NON_FOOD_KEYWORDS):
        return True
    words = set(re.split(r'[^a-zæøå]+', h))
    return bool(words & NON_FOOD_EXACT_WORDS)


# ─────────────────────────────────────────────────────────────────────────────
# Salling API-kvote optimering
#
# Salling Group API har en lav daglig kvote, så vi kan kun hente priser for en
# håndfuld nye EAN'er pr. dag. To greb maksimerer værdien af de få kald:
#   1. Basisvarer (mælk, brød, æg, smør …) hentes ALTID først — de vigtigste
#      varer for prissammenligning får pris hurtigst og forsvinder så fra
#      "missing"-listen (de genbruges fra Supabase ved næste kørsel).
#   2. Resten roteres dagligt, så vi ikke spilder kvoten på de samme EAN'er hver
#      dag — over tid dækkes hele kataloget gradvist i stedet for kun toppen.
# ─────────────────────────────────────────────────────────────────────────────
STAPLE_FOOD_KEYWORDS = {
    'mælk', 'minimælk', 'letmælk', 'sødmælk', 'skummetmælk', 'kærnemælk',
    'fløde', 'piskefløde', 'madlavningsfløde', 'creme fraiche', 'cremefraiche',
    'yoghurt', 'skyr', 'ymer', 'smør', 'kærgården', 'margarine', 'ost',
    'æg', 'brød', 'rugbrød', 'franskbrød', 'toastbrød', 'boller',
    'hakket', 'oksekød', 'svinekød', 'kylling', 'fisk', 'laks', 'pålæg',
    'leverpostej', 'kartoffel', 'kartofler', 'løg', 'gulerod', 'agurk', 'tomat',
    'ris', 'pasta', 'spaghetti', 'mel', 'havregryn', 'sukker', 'salt',
    'kaffe', 'te', 'olie', 'smørbar',
}


def prioritize_eans(missing: list[str], ean_to_name: dict[str, str]) -> list[str]:
    """Sortér manglende EAN'er: basisvarer først, resten roteret pr. dag.

    Bevarer alle elementer (ingen filtrering) — ændrer kun rækkefølgen, så de
    få daglige Salling-kald bruges mest værdifuldt og spredes over kataloget.
    """
    import datetime

    staples, rest = [], []
    for ean in missing:
        name = (ean_to_name.get(ean) or '').lower()
        (staples if any(kw in name for kw in STAPLE_FOOD_KEYWORDS) else rest).append(ean)

    if rest:
        # Rotér resten dagligt (deterministisk pr. dato) så coverage spredes.
        doy = datetime.date.today().timetuple().tm_yday
        offset = (doy * 137) % len(rest)
        rest = rest[offset:] + rest[:offset]

    return staples + rest
