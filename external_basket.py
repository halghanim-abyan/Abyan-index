"""Non-supermarket CPI representative basket.

These are not supermarket products and are not scraped by ``scraper.py``.
They represent CPI services and administered/market prices such as rent,
utilities, transport, health, education, hotels, restaurants, insurance,
and personal services. ``external_sources.py`` seeds them as index-base
observations until a live provider, official monthly sub-index, or manual
quote file is connected.
"""

from __future__ import annotations

from typing import Any


EXTERNAL_PROXY_SOURCE: dict[str, Any] = {
    "type": "external_proxy",
    "name": "External CPI Proxy",
    "price": 100.0,
    "unit": "index",
    "notes": (
        "Non-supermarket CPI representative item. Seeded at base 100 until "
        "a live provider, official monthly sub-index, or manual quote feed is connected."
    ),
}


def _item(name: str, category: str, weight: float) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "weight": weight,
        "urls": {},
        "source": dict(EXTERNAL_PROXY_SOURCE),
    }


def _weighted(category: str, names: list[str], total_weight: float) -> list[dict[str, Any]]:
    if not names:
        return []
    each = total_weight / len(names)
    return [_item(name, category, each) for name in names]


HOUSING_CITIES = [
    "Riyadh North", "Riyadh East", "Jeddah North", "Jeddah South",
    "Dammam", "Khobar", "Makkah", "Madinah", "Abha",
]
HOUSING_UNITS = ["Apartment 1BR", "Apartment 2BR", "Apartment 3BR", "Villa 4BR"]
HOUSING_ITEMS = [
    f"Residential rent - {unit} - {city}"
    for city in HOUSING_CITIES
    for unit in HOUSING_UNITS
]

UTILITIES_ITEMS = [
    "Electricity bill - apartment low usage", "Electricity bill - apartment medium usage",
    "Electricity bill - apartment high usage", "Electricity bill - villa medium usage",
    "Electricity bill - villa high usage", "Water bill - apartment low usage",
    "Water bill - apartment medium usage", "Water bill - villa medium usage",
    "Sewerage service charge - apartment", "Sewerage service charge - villa",
    "Gas cylinder refill - standard", "Gas cylinder delivery fee",
    "District cooling charge - apartment", "Building maintenance fee - apartment",
    "Home repair call-out fee", "Plumbing repair visit", "Electrical repair visit",
    "AC maintenance visit", "Pest control visit - apartment", "Waste collection service fee",
]

TRANSPORT_ITEMS = [
    "Gasoline 91 - liter", "Gasoline 95 - liter", "Diesel - liter",
    "Engine oil change - sedan", "Engine oil change - SUV", "Car wash - basic",
    "Car wash - premium", "Tire replacement - sedan single tire",
    "Tire replacement - SUV single tire", "Battery replacement - sedan",
    "Brake pads replacement - sedan", "Wheel alignment service", "Vehicle inspection fee",
    "Parking fee - city center hour", "Parking fee - mall hour",
    "Taxi fare - short urban trip", "Taxi fare - airport trip",
    "Ride hailing - short urban trip", "Ride hailing - medium urban trip",
    "Ride hailing - airport trip", "Public bus ticket - city route",
    "Metro ticket - single trip", "Intercity bus ticket - Riyadh Dammam",
    "Domestic flight - Riyadh Jeddah economy", "Domestic flight - Riyadh Dammam economy",
    "Domestic flight - Jeddah Abha economy", "Car rental - economy daily",
    "Car rental - SUV daily", "School transport fee - monthly",
    "Delivery courier fee - small parcel", "Vehicle insurance renewal - third party",
    "Vehicle registration renewal fee", "Roadside assistance annual plan",
    "Driving lesson fee - single session", "Taxi airport surcharge", "Intercity train ticket - economy",
]

COMMUNICATION_ITEMS = [
    "Mobile prepaid voice bundle", "Mobile prepaid data 10GB", "Mobile prepaid data 50GB",
    "Mobile postpaid basic plan", "Mobile postpaid family plan", "Fiber internet 100 Mbps",
    "Fiber internet 200 Mbps", "Fiber internet 500 Mbps", "Home 5G internet plan",
    "International call bundle", "Mobile device repair - screen", "Mobile device repair - battery",
    "SIM replacement fee", "Streaming video monthly subscription", "Cloud storage monthly subscription",
    "Landline monthly subscription", "Router replacement fee", "Smartphone mid-range model",
    "Smartphone charger accessory", "Wireless earbuds accessory",
]

HEALTH_ITEMS = [
    "General practitioner consultation", "Specialist consultation - internal medicine",
    "Specialist consultation - pediatrics", "Specialist consultation - dermatology",
    "Dental consultation", "Dental cleaning service", "Dental filling service",
    "Orthodontic monthly visit", "Eye examination", "Prescription glasses lenses",
    "Prescription glasses frame", "Contact lenses monthly pack", "Blood test - CBC",
    "Blood test - vitamin D", "X-ray scan", "Ultrasound scan", "Physiotherapy session",
    "Emergency room visit", "Pharmacy prescription medicine basket", "Pain reliever tablets",
    "Cold and flu medicine", "Antacid medicine", "Baby vaccination private clinic",
    "Maternity consultation", "Home nursing visit", "Medical insurance co-pay",
    "Hospital room daily charge", "Ambulance private transfer",
]

EDUCATION_ITEMS = [
    "Private kindergarten tuition - monthly", "Private primary school tuition - monthly",
    "Private intermediate school tuition - monthly", "Private secondary school tuition - monthly",
    "International school tuition - primary monthly", "International school tuition - secondary monthly",
    "University tuition - private semester", "Vocational training course",
    "English language course - monthly", "Computer skills course",
    "Tutoring session - mathematics", "Tutoring session - English",
    "School uniform - primary", "School uniform - secondary", "School backpack",
    "Textbook bundle - primary", "Textbook bundle - secondary", "Stationery bundle - student",
    "Exam fee - standardized test", "Nursery fee - daily", "Childcare fee - monthly",
    "Educational app subscription", "Printing and copying - student pack", "Graduation ceremony fee",
]

RECREATION_ITEMS = [
    "Cinema ticket - standard", "Cinema ticket - premium", "Gym membership - monthly",
    "Swimming pool entry", "Sports club membership - monthly", "Football field rental - hour",
    "Children indoor play ticket", "Theme park ticket", "Museum entry ticket",
    "Live event ticket - standard", "Book - paperback", "E-book purchase",
    "Newspaper monthly subscription", "Gaming subscription monthly", "Toy car", "Board game",
    "Bicycle maintenance", "Pet grooming service", "Photography service - basic",
    "Music lesson - single session", "Art class - single session", "Streaming music monthly subscription",
]

RESTAURANT_ITEMS = [
    "Restaurant meal - breakfast cafe", "Restaurant meal - fast food burger",
    "Restaurant meal - fast food chicken", "Restaurant meal - pizza medium",
    "Restaurant meal - shawarma sandwich", "Restaurant meal - rice and chicken",
    "Restaurant meal - seafood plate", "Restaurant meal - family casual dining",
    "Restaurant meal - business lunch", "Restaurant meal - fine dining main dish",
    "Coffee shop - espresso", "Coffee shop - latte", "Coffee shop - tea", "Coffee shop - pastry",
    "Juice bar - fresh juice", "Bakery - croissant", "Bakery - cake slice", "Ice cream cup",
    "Food delivery fee", "Catering meal per person", "School canteen meal",
    "Restaurant soft drink", "Restaurant bottled water", "Buffet meal - hotel restaurant",
]

HOTEL_ITEMS = [
    "Hotel room - Riyadh weekday 3 star", "Hotel room - Riyadh weekend 4 star",
    "Hotel room - Jeddah weekday 3 star", "Hotel room - Jeddah weekend 4 star",
    "Hotel room - Makkah weekday 3 star", "Hotel room - Makkah weekend 4 star",
    "Hotel room - Madinah weekday 3 star", "Hotel room - Dammam weekday 4 star",
    "Serviced apartment - Riyadh daily", "Serviced apartment - Jeddah daily",
    "Resort stay - weekend night", "Hotel breakfast add-on",
]

CLOTHING_ITEMS = [
    "Men thobe - standard", "Men shirt - casual", "Men trousers - casual",
    "Men jacket - light", "Men underwear pack", "Women abaya - standard",
    "Women dress - casual", "Women blouse - casual", "Women trousers - casual",
    "Women scarf", "Women underwear pack", "Child t-shirt", "Child trousers",
    "Child school uniform", "Baby onesie pack", "Sports shirt", "Sports trousers",
    "Laundry service - shirt", "Dry cleaning - suit", "Tailoring alteration",
    "Clothing repair", "Winter coat", "Socks pack", "Cap or hat",
]

FOOTWEAR_ITEMS = [
    "Men formal shoes", "Men sports shoes", "Men sandals", "Women formal shoes",
    "Women sports shoes", "Women sandals", "Child school shoes", "Child sports shoes",
    "Baby shoes", "Shoe repair service", "Shoe polish kit", "Insoles pair",
]

FURNITURE_ITEMS = [
    "Sofa - three seat", "Dining table - four seat", "Bed frame - queen", "Mattress - queen",
    "Wardrobe - two door", "Office chair", "Curtains - standard room", "Carpet - medium",
    "Refrigerator - medium", "Washing machine - front load", "Dishwasher - standard",
    "Microwave oven", "Electric kettle", "Vacuum cleaner", "Air conditioner split unit",
    "Air purifier", "LED TV - mid size", "Cookware set", "Dinnerware set",
    "Bedding set - queen", "Towel set", "Light bulb LED pack",
    "Home appliance repair visit", "Furniture delivery and assembly",
]

HOUSEHOLD_SERVICE_ITEMS = [
    "House cleaning visit - apartment", "House cleaning visit - villa",
    "Laundry service - kilogram", "Ironing service - shirt", "Carpet cleaning service",
    "Sofa cleaning service", "Domestic worker hourly service", "Domestic worker monthly agency fee",
    "Moving service - apartment", "Storage unit monthly fee", "Home internet installation visit",
    "Appliance installation visit", "Locksmith visit", "Glass repair service",
    "Gardening visit", "Pool maintenance visit",
]

INSURANCE_ITEMS = [
    "Health insurance premium - individual basic", "Health insurance premium - family basic",
    "Motor insurance premium - third party", "Motor insurance premium - comprehensive",
    "Home contents insurance annual", "Travel insurance - single trip",
    "Life insurance annual premium", "Medical malpractice insurance fee",
    "Device insurance annual", "Extended warranty - appliance", "Extended warranty - mobile phone",
    "Property insurance annual", "Personal accident insurance annual", "Insurance policy admin fee",
]

FINANCIAL_SERVICE_ITEMS = [
    "Bank account monthly fee", "ATM withdrawal fee", "Local transfer fee",
    "International remittance fee", "Credit card annual fee", "Credit card cash advance fee",
    "Brokerage trade commission", "Investment fund subscription fee",
    "Currency exchange spread proxy", "Loan processing admin fee",
]

PERSONAL_SERVICE_ITEMS = [
    "Men haircut", "Women haircut", "Hair coloring service", "Beard trim",
    "Manicure service", "Pedicure service", "Spa massage session",
    "Wedding hall rental - basic", "Photography studio portrait",
    "Legal document notarization service", "Government service typing fee",
    "Passport photo service", "Watch repair service", "Jewelry repair service",
    "Bag repair service", "Child haircut", "Salon blow dry", "Cosmetic consultation",
    "Personal care subscription box", "Funeral service basic package",
]

GROUPS: dict[str, tuple[float, list[str]]] = {
    "Housing": (3.20, HOUSING_ITEMS),
    "Utilities": (0.70, UTILITIES_ITEMS),
    "Transport": (1.60, TRANSPORT_ITEMS),
    "Communication": (0.95, COMMUNICATION_ITEMS),
    "Health": (0.85, HEALTH_ITEMS),
    "Education": (0.65, EDUCATION_ITEMS),
    "Recreation": (0.65, RECREATION_ITEMS),
    "Restaurants": (0.80, RESTAURANT_ITEMS),
    "Hotels": (0.25, HOTEL_ITEMS),
    "Clothing": (0.55, CLOTHING_ITEMS),
    "Footwear": (0.25, FOOTWEAR_ITEMS),
    "Furniture": (0.85, FURNITURE_ITEMS),
    "HouseholdServices": (0.45, HOUSEHOLD_SERVICE_ITEMS),
    "Insurance": (0.40, INSURANCE_ITEMS),
    "FinancialServices": (0.25, FINANCIAL_SERVICE_ITEMS),
    "PersonalServices": (0.45, PERSONAL_SERVICE_ITEMS),
}


def build_non_supermarket_basket() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for category, (total_weight, names) in GROUPS.items():
        out.extend(_weighted(category, names, total_weight))
    return out
