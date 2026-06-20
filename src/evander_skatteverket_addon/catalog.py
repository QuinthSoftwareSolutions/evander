from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ADDON_ID = "skatteverket-addon"
STATE_DB = "state.db"


@dataclass(frozen=True)
class SkatteverketApiCatalogEntry:
    name: str
    display_name: str
    version: str
    category: str
    api_type: str
    environments: tuple[str, ...] = ("komplett_testtjanst", "driftsatt_tjanst")
    oauth_scope: str | None = None
    form_fields: tuple[str, ...] = ()
    implemented_operations: tuple[str, ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "category": self.category,
            "api_type": self.api_type,
            "environments": list(self.environments),
            "oauth_scope": self.oauth_scope,
            "form_fields": list(self.form_fields),
            "implemented_operations": list(self.implemented_operations),
            "tags": list(self.tags),
        }


CATALOG: tuple[SkatteverketApiCatalogEntry, ...] = (
    SkatteverketApiCatalogEntry(
        name="skattekonto",
        display_name="Skattekonto",
        version="v2",
        category="redovisning",
        api_type="partner-api",
        oauth_scope="oauth2-ska",
        form_fields=("certifikatsubjekt", "redirect_uri"),
        implemented_operations=("read",),
        tags=("avgifter", "skatter", "redovisning"),
    ),
    SkatteverketApiCatalogEntry(
        name="beskattning-skattekonto-huvudmans-saldo-och-transaktioner",
        display_name="Skattekonto - hämta huvudmäns saldo och transaktioner",
        version="v1",
        category="redovisning",
        api_type="api",
        oauth_scope="oauth2-skahmst",
        implemented_operations=("read",),
        tags=("skatter", "redovisning"),
    ),
    SkatteverketApiCatalogEntry(
        name="momsdeklaration",
        display_name="Momsdeklaration",
        version="v1",
        category="deklaration",
        api_type="partner-api",
        oauth_scope="oauth2-momsdeklaration",
        form_fields=("certifikatsubjekt", "redirect_uri"),
        implemented_operations=("draft", "validate", "submit"),
        tags=("avgifter", "skatter", "redovisning"),
    ),
    SkatteverketApiCatalogEntry(
        name="inkomstdeklaration1",
        display_name="Inkomstdeklaration 1",
        version="v1",
        category="deklaration",
        api_type="partner-api",
        oauth_scope="oauth2-ink1",
        form_fields=("certifikatsubjekt", "redirect_uri"),
        implemented_operations=("draft", "validate", "submit"),
        tags=("redovisning",),
    ),
    SkatteverketApiCatalogEntry(
        name="inkomstdeklaration2-4",
        display_name="Inkomstdeklaration 2-4",
        version="v1",
        category="deklaration",
        api_type="partner-api",
        oauth_scope="oauth2-inkforetag",
        form_fields=("certifikatsubjekt", "redirect_uri"),
        implemented_operations=("draft", "validate", "submit"),
        tags=("redovisning",),
    ),
    SkatteverketApiCatalogEntry(
        name="arbetsgivardeklaration-inlamning",
        display_name="Arbetsgivardeklaration - inlämning",
        version="v1",
        category="deklaration",
        api_type="partner-api",
        oauth_scope="oauth2-agd",
        form_fields=("redirect_uri",),
        implemented_operations=("draft", "validate", "submit"),
        tags=("arbetsgivardeklaration", "löner", "avdrag", "avgifter"),
    ),
    SkatteverketApiCatalogEntry(
        name="arbetsgivardeklaration-hantera-redovisningsperiod",
        display_name="Arbetsgivardeklaration - hantera redovisningsperiod",
        version="v1",
        category="deklaration",
        api_type="partner-api",
        oauth_scope="oauth2-agdredovisningperiod",
        form_fields=("redirect_uri",),
        implemented_operations=("read",),
        tags=("arbetsgivardeklaration", "löner", "avgifter"),
    ),
    SkatteverketApiCatalogEntry(
        name="ombudshantering",
        display_name="Ombudshantering",
        version="v2",
        category="kontroll",
        api_type="partner-api",
        oauth_scope="oauth2-obr",
        form_fields=("certifikatsubjekt", "redirect_uri"),
        implemented_operations=("read",),
        tags=("ombud", "redovisning", "regler"),
    ),
    SkatteverketApiCatalogEntry(
        name="kundhandelser",
        display_name="Kundhändelser",
        version="v4",
        category="redovisning",
        api_type="partner-api",
        oauth_scope="oauth2-kundhandelser",
        implemented_operations=("read",),
        tags=("avgifter", "deklaration", "skatter", "redovisning"),
    ),
    SkatteverketApiCatalogEntry(
        name="mina-arenden-kundhandelser",
        display_name="Mina ärenden kundhändelser",
        version="v1",
        category="redovisning",
        api_type="partner-api",
        oauth_scope="oauth2-formedlingfraga",
        implemented_operations=("read",),
        tags=("avgifter", "deklaration", "skatter", "redovisning"),
    ),
    SkatteverketApiCatalogEntry(
        name="beskattningsengagemang",
        display_name="Beskattningsengagemang",
        version="v1",
        category="redovisning",
        api_type="partner-api",
        oauth_scope="oauth2-beskattningsengagema",
        form_fields=("redirect_uri",),
        implemented_operations=("read",),
        tags=("avgifter", "skatter", "redovisning"),
    ),
    SkatteverketApiCatalogEntry(
        name="fraga-om-skatteavdrag",
        display_name="Fråga om skatteavdrag",
        version="v2",
        category="löner",
        api_type="partner-api",
        oauth_scope="oauth2-fos",
        implemented_operations=("read",),
        tags=("a-skatt", "skattetabell", "avdrag", "löner"),
    ),
    SkatteverketApiCatalogEntry(
        name="fraga-om-skatteavdrag-i-kronor",
        display_name="Fråga om skatteavdrag i kronor",
        version="v1",
        category="löner",
        api_type="öppet-api",
        oauth_scope="oauth2-fosik",
        implemented_operations=("read",),
        tags=("a-skatt", "skattetabell", "avdrag", "löner"),
    ),
)

CATALOG_BY_NAME = {entry.name: entry for entry in CATALOG}
SUBMISSION_API_NAMES = frozenset(
    entry.name for entry in CATALOG if "submit" in entry.implemented_operations
)


def catalog_payload() -> dict[str, Any]:
    return {
        "addon_id": ADDON_ID,
        "apis": [entry.to_payload() for entry in CATALOG],
        "submission_api_names": sorted(SUBMISSION_API_NAMES),
    }
