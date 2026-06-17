"""Daily non-supermarket market source rules.

The official CPI proxy basket contains services and broad representative
goods. Only tangible goods should be routed to public e-commerce sources;
services such as rent, insurance, tuition, and medical visits stay on their
official/monthly source rows until a dedicated provider is connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class MarketSourceRule:
    query: str
    required_terms: tuple[str, ...] = ()
    reject_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class OfficialPriceRule:
    store_name: str
    price: float
    source_url: str
    observed_title: str
    notes: str


NOON_SEARCH_TEMPLATE = "https://www.noon.com/saudi-en/search/?q={query}"
AMAZON_SEARCH_TEMPLATE = "https://www.amazon.sa/s?k={query}"

SERA_ELECTRICITY_URL = "https://www.sera.gov.sa/en/consumer/electric-tariff"
MARAFIQ_WATER_TARIFF_URL = "https://www.marafiq.com.sa/en/partnering-with-us/water-tariff/"
STC_PREPAID_URL = "https://www.stc.com.sa/en/personal/mobile/packages/prepaid.html"
STC_POSTPAID_URL = "https://www.stc.com.sa/en/personal/mobile/packages/postpaid-backup.html"
STC_FIBER_URL = "https://www.stc.com.sa/en/personal/homepackages/baity-fiber-packages.html"
STC_TV_URL = "https://www.stc.com.sa/en/personal/lifestyle/stctv/stctv-home.html"
ZAIN_5G_HOME_URL = "https://shop.sa.zain.com/en/home-page/home-plans/5g/postpaid-5g-home-plans"
MVPI_FEES_URL = "https://www.mvpi.com.sa/en/services"
MOI_TRAFFIC_FEES_URL = (
    "https://www.moi.gov.sa/wps/portal/Home/sectors/publicsecurity/traffic/contents/"
)
RIYADH_BUS_SPA_URL = "https://www.spa.gov.sa/en/N1982450"
SPOTIFY_PREMIUM_URL = "https://www.spotify.com/sa-en/premium/"
APPLE_ICLOUD_URL = "https://support.apple.com/en-bw/108047"
REEL_CINEMA_FAQ_URL = "https://www.reelcinemas.com/en-sa/faq"
QIYAS_FEES_URL = "https://qiyas.sa/en/registration"


def _price_with_vat(amount: float) -> float:
    return round(amount * 1.15, 2)


def _electricity_bill(kwh: float) -> float:
    """Representative residential electricity bill, VAT included."""
    before_vat = min(kwh, 6000.0) * 0.18 + max(kwh - 6000.0, 0.0) * 0.30
    return _price_with_vat(before_vat)


def _water_component_bill(cubic_meters: float, component: str) -> float:
    """Representative residential water or sewerage bill, VAT included."""
    slabs = (
        (15.0, 0.10, 0.05),
        (15.0, 1.00, 0.50),
        (15.0, 3.00, 1.50),
        (15.0, 4.00, 2.00),
        (float("inf"), 6.00, 3.00),
    )
    remaining = cubic_meters
    before_vat = 0.0
    for limit, water_rate, sewer_rate in slabs:
        if remaining <= 0:
            break
        volume = min(remaining, limit)
        before_vat += volume * (sewer_rate if component == "sewer" else water_rate)
        remaining -= volume
    return _price_with_vat(before_vat)


NOON_ITEM_RULES: dict[str, MarketSourceRule] = {
    # Clothing
    "Men thobe - standard": MarketSourceRule("men thobe", required_terms=("thobe",)),
    "Men shirt - casual": MarketSourceRule("men shirt casual", required_terms=("shirt",)),
    "Men trousers - casual": MarketSourceRule("men trousers casual", required_terms=("trousers",)),
    "Men jacket - light": MarketSourceRule("men light jacket", required_terms=("jacket",)),
    "Men underwear pack": MarketSourceRule("men underwear pack", required_terms=("underwear",)),
    "Women abaya - standard": MarketSourceRule("women abaya", required_terms=("abaya",)),
    "Women dress - casual": MarketSourceRule("women dress casual", required_terms=("dress",)),
    "Women blouse - casual": MarketSourceRule("women blouse", required_terms=("blouse",)),
    "Women trousers - casual": MarketSourceRule("women trousers", required_terms=("trousers",)),
    "Women scarf": MarketSourceRule("women scarf", required_terms=("scarf",)),
    "Women underwear pack": MarketSourceRule("women underwear pack", required_terms=("underwear",)),
    "Child t-shirt": MarketSourceRule("kids t shirt", required_terms=("shirt",)),
    "Child trousers": MarketSourceRule("kids trousers", required_terms=("trousers",)),
    "Child school uniform": MarketSourceRule("school uniform", required_terms=("uniform",)),
    "Baby onesie pack": MarketSourceRule("baby onesie", required_terms=("onesie",)),
    "Sports shirt": MarketSourceRule("sports shirt men", required_terms=("shirt",)),
    "Sports trousers": MarketSourceRule("sports trousers", required_terms=("trousers",)),
    "Winter coat": MarketSourceRule("winter coat", required_terms=("coat",)),
    "Socks pack": MarketSourceRule("socks pack", required_terms=("socks",)),
    "Cap or hat": MarketSourceRule("cap hat", required_terms=("cap", "hat")),

    # Footwear
    "Men formal shoes": MarketSourceRule("men formal shoes", required_terms=("shoes",)),
    "Men sports shoes": MarketSourceRule("men sports shoes", required_terms=("shoes",)),
    "Men sandals": MarketSourceRule("men sandals", required_terms=("sandals",)),
    "Women formal shoes": MarketSourceRule("women formal shoes", required_terms=("shoes",)),
    "Women sports shoes": MarketSourceRule("women sports shoes", required_terms=("shoes",)),
    "Women sandals": MarketSourceRule("women sandals", required_terms=("sandals",)),
    "Child school shoes": MarketSourceRule("kids school shoes", required_terms=("shoes",)),
    "Child sports shoes": MarketSourceRule("kids sports shoes", required_terms=("shoes",)),
    "Baby shoes": MarketSourceRule("baby shoes", required_terms=("shoes",)),
    "Shoe polish kit": MarketSourceRule("shoe polish kit", required_terms=("polish",)),
    "Insoles pair": MarketSourceRule("shoe insoles pair", required_terms=("insoles",)),

    # Furniture, appliances, and household goods
    "Sofa - three seat": MarketSourceRule("three seat sofa", required_terms=("sofa",)),
    "Dining table - four seat": MarketSourceRule("dining table 4 seater", required_terms=("dining", "table")),
    "Bed frame - queen": MarketSourceRule("queen bed frame", required_terms=("bed", "frame")),
    "Mattress - queen": MarketSourceRule(
        "queen mattress",
        required_terms=("mattress",),
        reject_terms=("protector", "topper", "cover"),
    ),
    "Wardrobe - two door": MarketSourceRule("two door wardrobe", required_terms=("wardrobe",)),
    "Office chair": MarketSourceRule("office chair", required_terms=("chair",)),
    "Curtains - standard room": MarketSourceRule(
        "curtains",
        required_terms=("curtain",),
        reject_terms=("rod", "hook", "ring"),
    ),
    "Carpet - medium": MarketSourceRule(
        "medium carpet",
        required_terms=("carpet",),
        reject_terms=("cleaner", "brush", "tape"),
    ),
    "Refrigerator - medium": MarketSourceRule(
        "refrigerator",
        required_terms=("refrigerator",),
        reject_terms=("cover", "organizer", "magnet", "stand"),
    ),
    "Washing machine - front load": MarketSourceRule(
        "front load washing machine",
        required_terms=("washing", "machine"),
        reject_terms=("cover", "stand", "hose"),
    ),
    "Dishwasher - standard": MarketSourceRule("dishwasher", required_terms=("dishwasher",)),
    "Microwave oven": MarketSourceRule("microwave oven", required_terms=("microwave",)),
    "Electric kettle": MarketSourceRule("electric kettle", required_terms=("kettle",)),
    "Vacuum cleaner": MarketSourceRule("vacuum cleaner", required_terms=("vacuum",)),
    "Air conditioner split unit": MarketSourceRule(
        "split air conditioner",
        required_terms=("air", "conditioner"),
        reject_terms=("remote", "cover", "bracket", "cleaner", "filter"),
    ),
    "Air purifier": MarketSourceRule("air purifier", required_terms=("purifier",)),
    "LED TV - mid size": MarketSourceRule(
        "LED TV 55 inch",
        required_terms=("tv",),
        reject_terms=("cart", "stand", "bracket", "mount", "remote", "cover", "protector"),
    ),
    "Cookware set": MarketSourceRule("cookware set", required_terms=("cookware",)),
    "Dinnerware set": MarketSourceRule("dinnerware set", required_terms=("dinnerware",)),
    "Bedding set - queen": MarketSourceRule("bedding set queen", required_terms=("bedding",)),
    "Towel set": MarketSourceRule("towel set", required_terms=("towel",)),
    "Light bulb LED pack": MarketSourceRule("LED light bulb pack", required_terms=("bulb",)),

    # Communication/electronics
    "Smartphone mid-range model": MarketSourceRule(
        "Samsung Galaxy A16 128GB",
        required_terms=("samsung", "galaxy"),
        reject_terms=("case", "cover", "protector", "charger", "cable", "adapter"),
    ),
    "Smartphone charger accessory": MarketSourceRule(
        "USB C fast charger",
        required_terms=("charger",),
        reject_terms=("cable", "case", "cover"),
    ),
    "Wireless earbuds accessory": MarketSourceRule(
        "wireless earbuds",
        required_terms=("earbuds",),
        reject_terms=("case", "cover"),
    ),
    "Router replacement fee": MarketSourceRule("wifi router", required_terms=("router",)),

    # Education and recreation goods
    "School uniform - primary": MarketSourceRule("primary school uniform", required_terms=("uniform",)),
    "School uniform - secondary": MarketSourceRule("school uniform", required_terms=("uniform",)),
    "School backpack": MarketSourceRule("school backpack", required_terms=("backpack",)),
    "Stationery bundle - student": MarketSourceRule("student stationery set", required_terms=("stationery",)),
    "Book - paperback": MarketSourceRule(
        "paperback book",
        required_terms=("book",),
        reject_terms=("notebook", "diary", "binder"),
    ),
    "Gaming subscription monthly": MarketSourceRule("playstation gift card", required_terms=("gift", "card")),
    "Toy car": MarketSourceRule("toy car", required_terms=("toy", "car")),
    "Board game": MarketSourceRule("board game", required_terms=("game",)),

    # Health and automotive retail items
    "Prescription glasses frame": MarketSourceRule("eyeglasses frame", required_terms=("frame",)),
    "Contact lenses monthly pack": MarketSourceRule("contact lenses", required_terms=("lenses",)),
    "Pain reliever tablets": MarketSourceRule("panadol tablets", required_terms=("panadol",)),
    "Cold and flu medicine": MarketSourceRule("cold flu medicine", required_terms=("cold", "flu")),
    "Antacid medicine": MarketSourceRule("antacid tablets", required_terms=("antacid",)),
    "Engine oil change - sedan": MarketSourceRule(
        "engine oil 5w30",
        required_terms=("oil",),
        reject_terms=("filter", "cap", "funnel"),
    ),
    "Engine oil change - SUV": MarketSourceRule(
        "engine oil 5w30",
        required_terms=("oil",),
        reject_terms=("filter", "cap", "funnel"),
    ),
    "Tire replacement - sedan single tire": MarketSourceRule(
        "sedan car tire",
        required_terms=("tire",),
        reject_terms=("inflator", "gauge", "cover", "compressor"),
    ),
    "Tire replacement - SUV single tire": MarketSourceRule(
        "SUV tire",
        required_terms=("tire",),
        reject_terms=("inflator", "gauge", "cover", "compressor"),
    ),
    "Battery replacement - sedan": MarketSourceRule(
        "car battery",
        required_terms=("battery",),
        reject_terms=("charger", "jump", "cable"),
    ),
    "Brake pads replacement - sedan": MarketSourceRule("brake pads", required_terms=("brake", "pads")),
}


AMAZON_ITEM_RULES: dict[str, MarketSourceRule] = {
    "Al Khair Saudi Coffee Bahar 250g": MarketSourceRule(
        "saudi coffee bahar 250g",
        required_terms=("coffee", "saudi", "250"),
        reject_terms=("instant", "sachet", "sachets", "capsule", "capsules"),
    ),
    "Lipton Yellow Label Tea Bags 100P": MarketSourceRule(
        "Lipton Yellow Label Tea Bags 100",
        required_terms=("lipton", "yellow", "100"),
        reject_terms=("200", "loose", "green", "mint", "lemon", "herbal"),
    ),
    "Al Tayebat Arabic Pita Bread 6P": MarketSourceRule(
        "arabic bread 6",
        required_terms=("bread", "6"),
        reject_terms=("bag", "bags", "reusable", "container", "sourdough"),
    ),
    "Finish Quantum Dishwasher Tablets 32P": MarketSourceRule(
        "Finish Quantum 32 tablets",
        required_terms=("finish", "quantum", "32"),
        reject_terms=("x 3", "130", "112", "90", "70", "fairy"),
    ),
    "Sunbullah Frozen Minced Meat 400g": MarketSourceRule(
        "frozen minced meat 400g",
        required_terms=("minced", "400"),
        reject_terms=("chicken", "mortadella", "meatball", "meatballs", "sausage"),
    ),
    "Majdi Cardamom 50g": MarketSourceRule(
        "Majdi Cardamom 50g",
        required_terms=("majdi", "cardamom"),
        reject_terms=("powder", "500", "100"),
    ),
    "Krinos Halawa Tahini 500g": MarketSourceRule(
        "halawa tahini 500g",
        required_terms=("halawa", "500"),
        reject_terms=("tahina", "liquid", "1 kg", "1000"),
    ),
    "Local Fresh Potatoes 1kg": MarketSourceRule(
        "potatoes 1kg",
        required_terms=("potato", "1"),
        reject_terms=("500", "chips", "sweet"),
    ),
    "Textbook bundle - primary": MarketSourceRule(
        "primary school textbook",
        required_terms=("textbook",),
        reject_terms=("poster", "sticker"),
    ),
    "Textbook bundle - secondary": MarketSourceRule(
        "secondary school textbook",
        required_terms=("textbook",),
        reject_terms=("poster", "sticker"),
    ),
    "Printing and copying - student pack": MarketSourceRule(
        "A4 copy paper student pack",
        required_terms=("paper",),
        reject_terms=("thermal", "roll", "sticker", "photo"),
    ),
}


OFFICIAL_PRICE_RULES: dict[str, OfficialPriceRule] = {
    "Electricity bill - apartment low usage": OfficialPriceRule(
        store_name="SERA Electricity Tariff",
        price=_electricity_bill(300),
        source_url=SERA_ELECTRICITY_URL,
        observed_title="Residential electricity bill, 300 kWh representative monthly usage",
        notes="Official residential tariff applied to representative usage; VAT included.",
    ),
    "Electricity bill - apartment medium usage": OfficialPriceRule(
        store_name="SERA Electricity Tariff",
        price=_electricity_bill(900),
        source_url=SERA_ELECTRICITY_URL,
        observed_title="Residential electricity bill, 900 kWh representative monthly usage",
        notes="Official residential tariff applied to representative usage; VAT included.",
    ),
    "Electricity bill - apartment high usage": OfficialPriceRule(
        store_name="SERA Electricity Tariff",
        price=_electricity_bill(2500),
        source_url=SERA_ELECTRICITY_URL,
        observed_title="Residential electricity bill, 2500 kWh representative monthly usage",
        notes="Official residential tariff applied to representative usage; VAT included.",
    ),
    "Electricity bill - villa medium usage": OfficialPriceRule(
        store_name="SERA Electricity Tariff",
        price=_electricity_bill(2400),
        source_url=SERA_ELECTRICITY_URL,
        observed_title="Residential electricity bill, 2400 kWh representative monthly usage",
        notes="Official residential tariff applied to representative usage; VAT included.",
    ),
    "Electricity bill - villa high usage": OfficialPriceRule(
        store_name="SERA Electricity Tariff",
        price=_electricity_bill(6500),
        source_url=SERA_ELECTRICITY_URL,
        observed_title="Residential electricity bill, 6500 kWh representative monthly usage",
        notes="Official residential tariff applied to representative usage; VAT included.",
    ),
    "Water bill - apartment low usage": OfficialPriceRule(
        store_name="Saudi Water Tariff",
        price=_water_component_bill(10, "water"),
        source_url=MARAFIQ_WATER_TARIFF_URL,
        observed_title="Residential water bill, 10 m3 representative monthly usage",
        notes="Official residential water tariff applied to representative usage; VAT included.",
    ),
    "Water bill - apartment medium usage": OfficialPriceRule(
        store_name="Saudi Water Tariff",
        price=_water_component_bill(25, "water"),
        source_url=MARAFIQ_WATER_TARIFF_URL,
        observed_title="Residential water bill, 25 m3 representative monthly usage",
        notes="Official residential water tariff applied to representative usage; VAT included.",
    ),
    "Water bill - villa medium usage": OfficialPriceRule(
        store_name="Saudi Water Tariff",
        price=_water_component_bill(45, "water"),
        source_url=MARAFIQ_WATER_TARIFF_URL,
        observed_title="Residential water bill, 45 m3 representative monthly usage",
        notes="Official residential water tariff applied to representative usage; VAT included.",
    ),
    "Sewerage service charge - apartment": OfficialPriceRule(
        store_name="Saudi Water Tariff",
        price=_water_component_bill(25, "sewer"),
        source_url=MARAFIQ_WATER_TARIFF_URL,
        observed_title="Residential sewerage bill, 25 m3 representative monthly usage",
        notes="Official residential sewerage tariff applied to representative usage; VAT included.",
    ),
    "Sewerage service charge - villa": OfficialPriceRule(
        store_name="Saudi Water Tariff",
        price=_water_component_bill(45, "sewer"),
        source_url=MARAFIQ_WATER_TARIFF_URL,
        observed_title="Residential sewerage bill, 45 m3 representative monthly usage",
        notes="Official residential sewerage tariff applied to representative usage; VAT included.",
    ),
    "Gasoline 91 - liter": OfficialPriceRule(
        store_name="Saudi Aramco Retail Fuels",
        price=2.18,
        source_url="https://www.aramco.com/en/what-we-do/energy-products/retail-fuels",
        observed_title="Aramco Gasoline 91 official retail price per liter",
        notes="Official Saudi Aramco in-Kingdom retail fuel price, SAR/liter.",
    ),
    "Gasoline 95 - liter": OfficialPriceRule(
        store_name="Saudi Aramco Retail Fuels",
        price=2.33,
        source_url="https://www.aramco.com/en/what-we-do/energy-products/retail-fuels",
        observed_title="Aramco Gasoline 95 official retail price per liter",
        notes="Official Saudi Aramco in-Kingdom retail fuel price, SAR/liter.",
    ),
    "Diesel - liter": OfficialPriceRule(
        store_name="Saudi Aramco Retail Fuels",
        price=1.79,
        source_url="https://www.aramco.com/en/what-we-do/energy-products/retail-fuels",
        observed_title="Aramco Diesel official retail price per liter",
        notes="Official Saudi Aramco in-Kingdom retail fuel price, SAR/liter.",
    ),
    "Gas cylinder refill - standard": OfficialPriceRule(
        store_name="GASCO Official LPG Tariff",
        price=26.23,
        source_url="https://www.spa.gov.sa/en/N2479568",
        observed_title="GASCO 11kg LPG cylinder refill official unified price",
        notes="Official GASCO unified 11kg LPG cylinder refill price including transportation and VAT.",
    ),
    "Vehicle inspection fee": OfficialPriceRule(
        store_name="MVPI Official Fees",
        price=115.0,
        source_url=MVPI_FEES_URL,
        observed_title="MVPI periodic technical inspection fee for a passenger car",
        notes="Official periodic vehicle inspection fee; VAT included.",
    ),
    "Vehicle registration renewal fee": OfficialPriceRule(
        store_name="MOI Traffic Fees",
        price=100.0,
        source_url=MOI_TRAFFIC_FEES_URL,
        observed_title="Private vehicle registration renewal representative fee",
        notes="Official traffic-fee representative row for private vehicle renewal.",
    ),
    "Public bus ticket - city route": OfficialPriceRule(
        store_name="Riyadh Public Transport Fare",
        price=4.0,
        source_url=RIYADH_BUS_SPA_URL,
        observed_title="Riyadh city bus single trip fare",
        notes="Officially announced Riyadh public bus fare for a single city route trip.",
    ),
    "Metro ticket - single trip": OfficialPriceRule(
        store_name="Riyadh Public Transport Fare",
        price=4.0,
        source_url=RIYADH_BUS_SPA_URL,
        observed_title="Riyadh public transport single trip representative fare",
        notes="Riyadh public transport representative single-trip fare.",
    ),
    "SIM replacement fee": OfficialPriceRule(
        store_name="stc Official Packages",
        price=50.0,
        source_url=STC_POSTPAID_URL,
        observed_title="stc SIM replacement fee",
        notes="Official stc SIM replacement fee row.",
    ),
    "Mobile prepaid voice bundle": OfficialPriceRule(
        store_name="stc Official Packages",
        price=23.0,
        source_url=STC_PREPAID_URL,
        observed_title="stc prepaid local minutes bundle, representative 100-minute pack",
        notes="Official stc prepaid add-on representative voice bundle; VAT included where shown.",
    ),
    "Mobile prepaid data 10GB": OfficialPriceRule(
        store_name="stc Official Packages",
        price=74.75,
        source_url=STC_PREPAID_URL,
        observed_title="stc Sawa Flex 65 representative 10GB prepaid package",
        notes="Official stc prepaid data package representative row; VAT included.",
    ),
    "Mobile prepaid data 50GB": OfficialPriceRule(
        store_name="stc Official Packages",
        price=258.75,
        source_url=STC_PREPAID_URL,
        observed_title="stc prepaid large data package representative 45GB+ social bundle",
        notes="Official stc prepaid large data package representative row; VAT included.",
    ),
    "International call bundle": OfficialPriceRule(
        store_name="stc Official Packages",
        price=74.75,
        source_url=STC_PREPAID_URL,
        observed_title="stc Sawa Flex 65 representative package with international minutes",
        notes="Official stc prepaid bundle with international destinations; VAT included.",
    ),
    "Mobile postpaid basic plan": OfficialPriceRule(
        store_name="stc Official Packages",
        price=90.0,
        source_url=STC_POSTPAID_URL,
        observed_title="stc Mofawtar Basic+ monthly plan",
        notes="Official stc postpaid representative basic plan.",
    ),
    "Mobile postpaid family plan": OfficialPriceRule(
        store_name="stc Official Packages",
        price=517.5,
        source_url=STC_POSTPAID_URL,
        observed_title="stc Mofawtar 4 representative family/high-usage postpaid plan",
        notes="Official stc postpaid representative family/high-usage plan; VAT included.",
    ),
    "Fiber internet 500 Mbps": OfficialPriceRule(
        store_name="stc Official Packages",
        price=402.5,
        source_url=STC_FIBER_URL,
        observed_title="stc baity fiber 500 Mbps representative monthly plan",
        notes="Official stc home fiber 500 Mbps representative monthly plan; VAT included.",
    ),
    "Landline monthly subscription": OfficialPriceRule(
        store_name="stc Official Packages",
        price=113.85,
        source_url=STC_POSTPAID_URL,
        observed_title="stc Home Phone Plus monthly subscription",
        notes="Official stc fixed voice representative monthly subscription; VAT included.",
    ),
    "Streaming video monthly subscription": OfficialPriceRule(
        store_name="stc Official Packages",
        price=15.0,
        source_url=STC_TV_URL,
        observed_title="stc tv home representative monthly package",
        notes="Official stc tv representative video subscription row.",
    ),
    "Home 5G internet plan": OfficialPriceRule(
        store_name="Zain Official Packages",
        price=239.0,
        source_url=ZAIN_5G_HOME_URL,
        observed_title="Zain 5G home monthly representative plan",
        notes="Official Zain home 5G representative monthly plan.",
    ),
    "Cloud storage monthly subscription": OfficialPriceRule(
        store_name="Apple iCloud+ Saudi Arabia",
        price=3.99,
        source_url=APPLE_ICLOUD_URL,
        observed_title="Apple iCloud+ 50GB monthly subscription",
        notes="Official Apple iCloud+ representative monthly storage tier.",
    ),
    "Streaming music monthly subscription": OfficialPriceRule(
        store_name="Spotify Saudi Arabia",
        price=23.99,
        source_url=SPOTIFY_PREMIUM_URL,
        observed_title="Spotify Premium Individual monthly plan",
        notes="Official Spotify Saudi Arabia representative monthly music subscription.",
    ),
    "Cinema ticket - standard": OfficialPriceRule(
        store_name="Reel Cinemas KSA",
        price=45.0,
        source_url=REEL_CINEMA_FAQ_URL,
        observed_title="Reel Cinemas standard ticket representative price",
        notes="Published cinema ticket representative row.",
    ),
    "Exam fee - standardized test": OfficialPriceRule(
        store_name="Qiyas Official Fees",
        price=150.0,
        source_url=QIYAS_FEES_URL,
        observed_title="Qiyas standardized test representative registration fee",
        notes="Official Qiyas representative standardized exam registration fee.",
    ),
}


def noon_rule_for_item(item_name: str) -> MarketSourceRule | None:
    return NOON_ITEM_RULES.get(item_name)


def amazon_rule_for_item(item_name: str) -> MarketSourceRule | None:
    return AMAZON_ITEM_RULES.get(item_name)


def official_price_rule_for_item_store(
    item_name: str,
    store_name: str,
) -> OfficialPriceRule | None:
    rule = OFFICIAL_PRICE_RULES.get(item_name)
    if rule is None or rule.store_name != store_name:
        return None
    return rule


def daily_market_urls_for_item(item_name: str) -> dict[str, str]:
    urls: dict[str, str] = {}
    noon_rule = noon_rule_for_item(item_name)
    if noon_rule is not None:
        urls["Noon"] = NOON_SEARCH_TEMPLATE.format(query=quote_plus(noon_rule.query))

    amazon_rule = amazon_rule_for_item(item_name)
    if amazon_rule is not None:
        urls["Amazon"] = AMAZON_SEARCH_TEMPLATE.format(query=quote_plus(amazon_rule.query))

    official_rule = OFFICIAL_PRICE_RULES.get(item_name)
    if official_rule is not None:
        urls[official_rule.store_name] = official_rule.source_url

    return urls
