import os

NON_FOOD_KEYWORDS = {
    # Hygiejne & pleje
    'indlæg', 'batteri', 'shampoo', 'balsam', 'creme', 'lotion', 'bleer',
    'bleposer', 'vaskeserviet', 'vådserviet', 'skumvaskeklud', 'sutteflaske',
    'tandpasta', 'tandbørste', 'håndsæbe', 'shower gel', 'showergel', 'deodorant',
    'deospray', 'bind', 'tampon', 'babypads', 'babybleer',
    'solcreme', 'sollotion', 'solspray', 'solfaktor', 'hudpleje',
    'mascara', 'neglelak', 'parfume', 'makeupfjerner', 'brusegel',
    # Kæledyr
    'hundemad', 'kattefoder', 'kattemad', 'hundesnack', 'kattegrus', 'pedigree',
    'whiskas', 'felix', 'royal canin', 'purina', 'dreamies', 'friskies',
    'kattesand', 'kattebakke', 'hundelegetøj', 'kattemøbel',
    # Rengøring
    'opvaskemiddel', 'vaskemiddel', 'skyllemiddel', 'opvasketabs', 'vaskekapsler',
    'vaske-middel', 'toiletrengøring', 'rengøring', 'bref', 'domestos', 'harpic',
    'håndopvask', 'tabs', 'scrub daddy', 'vileda', 'skuresvampe',
    'tøjvask', 'tøjrens', 'pletfjerner',
    # Tobak
    'tobak', 'cigaret', 'cigarillo', 'snus', 'nikotin', 'tændstik',
    'lighter', 'fyrstikker',
    # Papirvarer
    'toiletpapir', 'køkkenrulle', 'køkken rulle', 'bagepapir', 'kleenex',
    # Planter & blomster
    'plante', 'planter', 'potte', 'potteskjuler', 'blomst', 'blomster',
    'buket', 'roser', 'tulipaner', 'orkidé', 'krysantemum', 'jord', 'gødning',
    # Lys
    'fyrfadslys', 'stearinlys', 'kronelys', 'bloklys', 'levende lys',
    # Maskiner & elektronik
    'kaffemaskine', 'kaffemaskiner', 'espressomaskine', 'kapselmaskine',
    'elkedel', 'airfryer', 'robotplæneklipper', 'støvsuger', 'strygerobot',
    'støvsugerpose', 'højtaler', 'mobiltilbehør',
    # Køkkengrej & husholdning
    'stegepande', 'tørrestativ', 'termokande', 'opbevaring', 'kurv',
    'tramontina', 'smartstore', 'husholdningsprodukter',
    # Tøj, sko & sport
    'sneakers', 'nike', 'hummel', 'friends', 'latz', 'jackpot', 't-shirt',
    'solbriller', 'sommerhat', 'gummisko', 'strandtaske', 'leggings',
    'badebukser', 'badetøj', 'badedragt',
    # Udendørs & fritid
    'solseng', 'parasol', 'badeklæde', 'fuglebad', 'fiskegrej', 'høreværn',
    'badevinger', 'badedyr', 'strandbold', 'kuglepistol', 'fodbold',
    # Soveværelse & tekstiler
    'sengetøj', 'sengetæppe', 'gavlpude', 'dørmåtte',
    # Legetøj & hobby
    'hot wheels', 'legetøj', 'kridt', 'strandkridt', 'jumbo',
    # Kosttilskud & sundhed
    'vitaminer', 'livol', 'gerimax', 'kosttilskud', 'proteinpulver',
    'whey protein', 'kreatin', 'collagen', 'omega-3 kapsler',
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
