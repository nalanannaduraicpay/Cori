"""ISO 4217 currency reference data: minor units, symbols, country defaults.

The DB table CURRENCIES is seeded from this module; at runtime the DB is the
source of truth, this module is the fallback used by pure functions and tests.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrencyInfo:
    code: str
    name: str
    minor_units: int
    symbol: str | None = None


# Minor units that are NOT 2.
_MINOR_UNIT_EXCEPTIONS = {
    # zero decimals
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0, "KRW": 0,
    "PYG": 0, "RWF": 0, "UGX": 0, "UYI": 0, "VND": 0, "VUV": 0, "XAF": 0, "XOF": 0, "XPF": 0,
    # three decimals
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
    # four decimals
    "CLF": 4, "UYW": 4,
}

_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "$", "AUD": "$", "CHF": "CHF",
    "INR": "₹", "SGD": "$", "MXN": "$", "BRL": "R$", "CNY": "¥", "HKD": "$", "NZD": "$",
    "SEK": "kr", "NOK": "kr", "DKK": "kr", "KRW": "₩", "ZAR": "R", "PLN": "zł", "TRY": "₺",
    "ILS": "₪", "THB": "฿", "PHP": "₱", "CZK": "Kč", "HUF": "Ft", "KWD": "KD", "AED": "AED",
    "SAR": "SAR", "RUB": "₽", "ISK": "kr",
}

# Active ISO 4217 codes and English names.
_NAMES = {
    "AED": "UAE Dirham", "AFN": "Afghani", "ALL": "Lek", "AMD": "Armenian Dram",
    "ANG": "Netherlands Antillean Guilder", "AOA": "Kwanza", "ARS": "Argentine Peso",
    "AUD": "Australian Dollar", "AWG": "Aruban Florin", "AZN": "Azerbaijan Manat",
    "BAM": "Convertible Mark", "BBD": "Barbados Dollar", "BDT": "Taka", "BGN": "Bulgarian Lev",
    "BHD": "Bahraini Dinar", "BIF": "Burundi Franc", "BMD": "Bermudian Dollar",
    "BND": "Brunei Dollar", "BOB": "Boliviano", "BRL": "Brazilian Real", "BSD": "Bahamian Dollar",
    "BTN": "Ngultrum", "BWP": "Pula", "BYN": "Belarusian Ruble", "BZD": "Belize Dollar",
    "CAD": "Canadian Dollar", "CDF": "Congolese Franc", "CHF": "Swiss Franc",
    "CLF": "Unidad de Fomento", "CLP": "Chilean Peso", "CNY": "Yuan Renminbi",
    "COP": "Colombian Peso", "CRC": "Costa Rican Colon", "CUP": "Cuban Peso",
    "CVE": "Cabo Verde Escudo", "CZK": "Czech Koruna", "DJF": "Djibouti Franc",
    "DKK": "Danish Krone", "DOP": "Dominican Peso", "DZD": "Algerian Dinar",
    "EGP": "Egyptian Pound", "ERN": "Nakfa", "ETB": "Ethiopian Birr", "EUR": "Euro",
    "FJD": "Fiji Dollar", "FKP": "Falkland Islands Pound", "GBP": "Pound Sterling",
    "GEL": "Lari", "GHS": "Ghana Cedi", "GIP": "Gibraltar Pound", "GMD": "Dalasi",
    "GNF": "Guinean Franc", "GTQ": "Quetzal", "GYD": "Guyana Dollar", "HKD": "Hong Kong Dollar",
    "HNL": "Lempira", "HTG": "Gourde", "HUF": "Forint", "IDR": "Rupiah",
    "ILS": "New Israeli Sheqel", "INR": "Indian Rupee", "IQD": "Iraqi Dinar", "IRR": "Iranian Rial",
    "ISK": "Iceland Krona", "JMD": "Jamaican Dollar", "JOD": "Jordanian Dinar", "JPY": "Yen",
    "KES": "Kenyan Shilling", "KGS": "Som", "KHR": "Riel", "KMF": "Comorian Franc",
    "KPW": "North Korean Won", "KRW": "Won", "KWD": "Kuwaiti Dinar", "KYD": "Cayman Islands Dollar",
    "KZT": "Tenge", "LAK": "Lao Kip", "LBP": "Lebanese Pound", "LKR": "Sri Lanka Rupee",
    "LRD": "Liberian Dollar", "LSL": "Loti", "LYD": "Libyan Dinar", "MAD": "Moroccan Dirham",
    "MDL": "Moldovan Leu", "MGA": "Malagasy Ariary", "MKD": "Denar", "MMK": "Kyat",
    "MNT": "Tugrik", "MOP": "Pataca", "MRU": "Ouguiya", "MUR": "Mauritius Rupee",
    "MVR": "Rufiyaa", "MWK": "Malawi Kwacha", "MXN": "Mexican Peso", "MYR": "Malaysian Ringgit",
    "MZN": "Mozambique Metical", "NAD": "Namibia Dollar", "NGN": "Naira", "NIO": "Cordoba Oro",
    "NOK": "Norwegian Krone", "NPR": "Nepalese Rupee", "NZD": "New Zealand Dollar",
    "OMR": "Rial Omani", "PAB": "Balboa", "PEN": "Sol", "PGK": "Kina", "PHP": "Philippine Peso",
    "PKR": "Pakistan Rupee", "PLN": "Zloty", "PYG": "Guarani", "QAR": "Qatari Rial",
    "RON": "Romanian Leu", "RSD": "Serbian Dinar", "RUB": "Russian Ruble", "RWF": "Rwanda Franc",
    "SAR": "Saudi Riyal", "SBD": "Solomon Islands Dollar", "SCR": "Seychelles Rupee",
    "SDG": "Sudanese Pound", "SEK": "Swedish Krona", "SGD": "Singapore Dollar",
    "SHP": "Saint Helena Pound", "SLE": "Leone", "SOS": "Somali Shilling", "SRD": "Surinam Dollar",
    "SSP": "South Sudanese Pound", "STN": "Dobra", "SVC": "El Salvador Colon", "SYP": "Syrian Pound",
    "SZL": "Lilangeni", "THB": "Baht", "TJS": "Somoni", "TMT": "Turkmenistan New Manat",
    "TND": "Tunisian Dinar", "TOP": "Pa'anga", "TRY": "Turkish Lira",
    "TTD": "Trinidad and Tobago Dollar", "TWD": "New Taiwan Dollar", "TZS": "Tanzanian Shilling",
    "UAH": "Hryvnia", "UGX": "Uganda Shilling", "USD": "US Dollar", "UYI": "Uruguay Peso en Unidades Indexadas",
    "UYU": "Peso Uruguayo", "UYW": "Unidad Previsional", "UZS": "Uzbekistan Sum",
    "VED": "Bolivar Soberano", "VES": "Bolivar Soberano", "VND": "Dong", "VUV": "Vatu",
    "WST": "Tala", "XAF": "CFA Franc BEAC", "XCD": "East Caribbean Dollar", "XOF": "CFA Franc BCEAO",
    "XPF": "CFP Franc", "YER": "Yemeni Rial", "ZAR": "Rand", "ZMW": "Zambian Kwacha",
    "ZWG": "Zimbabwe Gold",
}

CURRENCIES: dict[str, CurrencyInfo] = {
    code: CurrencyInfo(code, name, _MINOR_UNIT_EXCEPTIONS.get(code, 2), _SYMBOLS.get(code))
    for code, name in _NAMES.items()
}

# Currencies seeded with FX history for the demo.
DEMO_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "INR", "SGD", "MXN", "BRL", "KWD"]

# Raw symbol / token -> possible ISO codes. Order of candidates is not a preference.
SYMBOL_CANDIDATES: dict[str, list[str]] = {
    "$": ["USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN", "ARS", "CLP", "COP"],
    "US$": ["USD"], "U$S": ["USD"], "C$": ["CAD"], "CA$": ["CAD"], "CAN$": ["CAD"],
    "A$": ["AUD"], "AU$": ["AUD"], "NZ$": ["NZD"], "S$": ["SGD"], "SG$": ["SGD"],
    "HK$": ["HKD"], "MX$": ["MXN"], "MEX$": ["MXN"], "R$": ["BRL"],
    "€": ["EUR"], "£": ["GBP"], "¥": ["JPY", "CNY"], "円": ["JPY"], "元": ["CNY"], "RMB": ["CNY"],
    "₹": ["INR"], "RS": ["INR", "PKR", "LKR", "NPR"], "RS.": ["INR", "PKR", "LKR", "NPR"],
    "₩": ["KRW"], "FR.": ["CHF"], "SFR": ["CHF"], "KR": ["SEK", "NOK", "DKK", "ISK"],
    "KR.": ["SEK", "NOK", "DKK", "ISK"], "ZŁ": ["PLN"], "₽": ["RUB"], "₺": ["TRY"],
    "R": ["ZAR"], "₪": ["ILS"], "฿": ["THB"], "₱": ["PHP"], "KČ": ["CZK"], "FT": ["HUF"],
    "KD": ["KWD"],
}

COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "CA": "CAD", "GB": "GBP", "UK": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR",
    "ES": "EUR", "IT": "EUR", "IE": "EUR", "BE": "EUR", "AT": "EUR", "PT": "EUR", "FI": "EUR",
    "LU": "EUR", "GR": "EUR", "JP": "JPY", "AU": "AUD", "NZ": "NZD", "CH": "CHF", "IN": "INR",
    "SG": "SGD", "MX": "MXN", "BR": "BRL", "CN": "CNY", "HK": "HKD", "SE": "SEK", "NO": "NOK",
    "DK": "DKK", "KR": "KRW", "ZA": "ZAR", "KW": "KWD", "AE": "AED", "SA": "SAR", "PL": "PLN",
}

COUNTRY_NAMES: dict[str, str] = {
    "united states": "US", "usa": "US", "united states of america": "US", "canada": "CA",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "germany": "DE", "deutschland": "DE",
    "france": "FR", "netherlands": "NL", "the netherlands": "NL", "spain": "ES", "italy": "IT",
    "ireland": "IE", "belgium": "BE", "austria": "AT", "japan": "JP", "australia": "AU",
    "new zealand": "NZ", "switzerland": "CH", "india": "IN", "singapore": "SG", "mexico": "MX",
    "brazil": "BR", "china": "CN", "hong kong": "HK", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "south korea": "KR", "south africa": "ZA", "kuwait": "KW",
}


def get_currency(code: str | None) -> CurrencyInfo | None:
    if not code:
        return None
    return CURRENCIES.get(code.strip().upper())


def is_iso_code(code: str | None) -> bool:
    return get_currency(code) is not None


def minor_units(code: str | None, default: int = 2) -> int:
    info = get_currency(code)
    return info.minor_units if info else default


def country_to_iso2(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if len(v) == 2 and v.isalpha():
        return "GB" if v.upper() == "UK" else v.upper()
    return COUNTRY_NAMES.get(v.lower())
