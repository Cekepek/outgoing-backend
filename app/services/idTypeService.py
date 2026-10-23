from enum import Enum
from typing import Any, Optional


class CustomerIdType(str, Enum):
    PASSPORT = "PASSPORT"
    NATIONAL_ID = "NATIONAL_ID"
    DRIVING_LICENSE = "DRIVING_LICENSE"
    SOCIAL_SECURITY = "SOCIAL_SECURITY"
    TAX_ID = "TAX_ID"
    SENIOR_CITIZEN_ID = "SENIOR_CITIZEN_ID"
    BIRTH_CERTIFICATE = "BIRTH_CERTIFICATE"
    VILLAGE_ELDER_ID = "VILLAGE_ELDER_ID"
    RESIDENT_CARD = "RESIDENT_CARD"
    ALIEN_REGISTRATION = "ALIEN_REGISTRATION"
    PAN_CARD = "PAN_CARD"
    VOTERS_ID = "VOTERS_ID"
    HEALTH_CARD = "HEALTH_CARD"
    EMPLOYER_ID = "EMPLOYER_ID"
    OTHER = "OTHER"


TRANSFERKU_ID_TYPE_DETAILS: list[dict[str, str]] = [
    {"value": CustomerIdType.PASSPORT.value, "label": "Passport", "description": "Passport"},
    {"value": CustomerIdType.NATIONAL_ID.value, "label": "National Identification Card", "description": "National Identification Card"},
    {"value": CustomerIdType.DRIVING_LICENSE.value, "label": "Driving License", "description": "Driving License"},
    {"value": CustomerIdType.SOCIAL_SECURITY.value, "label": "Social Security Card/Number", "description": "Social Security Card/Number"},
    {"value": CustomerIdType.TAX_ID.value, "label": "Tax Payer Identification Card/Number", "description": "Tax Payer Identification Card/Number"},
    {"value": CustomerIdType.SENIOR_CITIZEN_ID.value, "label": "Senior Citizen Identification Card", "description": "Senior Citizen Identification Card"},
    {"value": CustomerIdType.BIRTH_CERTIFICATE.value, "label": "Birth Certificate", "description": "Birth Certificate"},
    {"value": CustomerIdType.VILLAGE_ELDER_ID.value, "label": "Village Elder Identification Card", "description": "Village Elder Identification Card"},
    {"value": CustomerIdType.RESIDENT_CARD.value, "label": "Permanent Residency Identification Card", "description": "Permanent Residency Identification Card"},
    {"value": CustomerIdType.ALIEN_REGISTRATION.value, "label": "Alien Registration Certificate/Card", "description": "Alien Registration Certificate/Card"},
    {"value": CustomerIdType.PAN_CARD.value, "label": "PAN Card", "description": "PAN Card"},
    {"value": CustomerIdType.VOTERS_ID.value, "label": "Voter’s Identification Card", "description": "Voter’s Identification Card"},
    {"value": CustomerIdType.HEALTH_CARD.value, "label": "Health Insurance Card/Number", "description": "Health Insurance Card/Number"},
    {"value": CustomerIdType.EMPLOYER_ID.value, "label": "Employer Identification Card", "description": "Employer Identification Card"},
    {"value": CustomerIdType.OTHER.value, "label": "Others", "description": "Others"},
]

# Static base mapping from Canonical / Transferku -> Lightremit code
_CANONICAL_TO_LIGHTREMIT: dict[str, str] = {
    CustomerIdType.PASSPORT.value: "01",
    CustomerIdType.NATIONAL_ID.value: "02",  # Default KTP; dynamically adjusted for foreigners
    CustomerIdType.DRIVING_LICENSE.value: "03",
    CustomerIdType.RESIDENT_CARD.value: "04",
    CustomerIdType.ALIEN_REGISTRATION.value: "04",
    CustomerIdType.SOCIAL_SECURITY.value: "99",
    CustomerIdType.TAX_ID.value: "99",
    CustomerIdType.SENIOR_CITIZEN_ID.value: "99",
    CustomerIdType.BIRTH_CERTIFICATE.value: "99",
    CustomerIdType.VILLAGE_ELDER_ID.value: "99",
    CustomerIdType.PAN_CARD.value: "99",
    CustomerIdType.VOTERS_ID.value: "99",
    CustomerIdType.HEALTH_CARD.value: "99",
    CustomerIdType.EMPLOYER_ID.value: "99",
    CustomerIdType.OTHER.value: "99",
}

# Mapping from Lightremit code -> Canonical / Transferku
_LIGHTREMIT_TO_CANONICAL: dict[str, str] = {
    "01": CustomerIdType.PASSPORT.value,          # Foreign Nationality
    "02": CustomerIdType.NATIONAL_ID.value,       # Indonesian Nationality (KTP)
    "03": CustomerIdType.DRIVING_LICENSE.value,   # Driving License
    "04": CustomerIdType.RESIDENT_CARD.value,     # Residence
    "99": CustomerIdType.OTHER.value,             # Other
}


def to_lightremit_id_type(
    id_type: Optional[str],
    nationality: Optional[str] = None,
    id_issue_country: Optional[str] = None,
) -> str:
    """
    Maps canonical/Transferku ID type to Lightremit code ('01', '02', '03', '04', '99').
    Performs context-aware detection for NATIONAL_ID (Indonesian vs Foreign Nationality).
    """
    if not id_type:
        return "02"

    raw = str(id_type).strip()

    # If already a valid Lightremit code
    if raw in _LIGHTREMIT_TO_CANONICAL:
        return raw

    normalized = raw.upper()

    # Context-aware logic for NATIONAL_ID:
    # If customer is non-Indonesian, their national ID maps to 01 (Foreign Nationality)
    if normalized == CustomerIdType.NATIONAL_ID.value:
        country = str(id_issue_country or nationality or "IDN").strip().upper()
        return "02" if country in ("IDN", "INDONESIA") else "01"

    return _CANONICAL_TO_LIGHTREMIT.get(normalized, "99")


def to_transferku_id_type(id_type: Optional[str]) -> str:
    """
    Ensures the ID type is a valid Transferku canonical enum string.
    Converts Lightremit codes ('01', '02', '03', '04', '99') to canonical strings.
    """
    if not id_type:
        return CustomerIdType.NATIONAL_ID.value

    raw = str(id_type).strip()

    # If it is a Lightremit code
    if raw in _LIGHTREMIT_TO_CANONICAL:
        return _LIGHTREMIT_TO_CANONICAL[raw]

    normalized = raw.upper()

    # If it's already one of the Transferku canonical enums
    if normalized in _CANONICAL_TO_LIGHTREMIT:
        return normalized

    return CustomerIdType.OTHER.value


def get_canonical_id_types() -> list[dict[str, str]]:
    """Return all Transferku / Canonical Customer ID types."""
    return list(TRANSFERKU_ID_TYPE_DETAILS)
