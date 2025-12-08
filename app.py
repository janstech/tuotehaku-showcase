"""
Tuotehaku – showcase-versio (FastAPI)

Tämä tiedosto on anonymisoitu ja yksinkertaistettu esimerkki oikeasta
tuotehaku-järjestelmästä. Tarkoitus on näyttää arkkitehtuuri ja
koodityyli, ei valmis tuotantokoodi.

Teknologiat:
- FastAPI
- Pydantic-mallit (validaatio)
- "Service layer" -rakenne hakulogiikalle
"""

from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="Tuotehaku – Showcase")


# ---------------------------------------------------------------------------
# Pydantic-mallit
# ---------------------------------------------------------------------------

class Product(BaseModel):
    sku: str = Field(..., description="Tuotteen yksilöivä koodi")
    name: str
    brand: Optional[str] = None
    price: float
    in_stock: bool
    supplier: str


class ProductQuery(BaseModel):
    """
    Hakuehdot – vastaava logiikka kuin oikeassa järjestelmässä, mutta
    supistettuna. Todellisessa järjestelmässä tässä voisi olla mm.
    tuoteryhmät, valmistajat, hintahaarukka, rajaukset varastosaldoon jne.
    """
    search: str = Field(..., description="Vapaa hakusana")
    max_results: int = Field(50, ge=1, le=200)
    only_in_stock: bool = False


class SearchResponse(BaseModel):
    total: int
    results: List[Product]


# ---------------------------------------------------------------------------
# Mockattu "service layer"
# ---------------------------------------------------------------------------

class ProductSearchService:
    """
    Showcase-versiossa käytetään kovakoodattua listaa tuotteita.
    Oikeassa järjestelmässä tämä luokka:
      - lukisi MySQL-tietokantaa
      - kutsuisi toimittajien rajapintoja
      - tekisi välimuistituksia jne.
    """

    def __init__(self) -> None:
        self._products = [
            Product(
                sku="ABC-001",
                name="USB-C kaapeli 1m",
                brand="Generic",
                price=9.90,
                in_stock=True,
                supplier="DemoSupplier",
            ),
            Product(
                sku="ABC-002",
                name="USB-C laturi 65W",
                brand="PowerBrand",
                price=39.90,
                in_stock=False,
                supplier="DemoSupplier",
            ),
            Product(
                sku="ABC-003",
                name="Langaton hiiri",
                brand="Clicky",
                price=24.90,
                in_stock=True,
                supplier="DemoSupplier",
            ),
        ]

    def search_products(self, query: ProductQuery) -> SearchResponse:
        # Todellisessa versiossa tämä olisi SQL / Elasticsearch / tms.
        term = query.search.lower().strip()

        filtered = [
            p
            for p in self._products
            if term in p.name.lower() or term in p.sku.lower()
        ]

        if query.only_in_stock:
            filtered = [p for p in filtered if p.in_stock]

        # Rajataan määrä, mutta palautetaan myös total
        total = len(filtered)
        limited = filtered[: query.max_results]

        return SearchResponse(total=total, results=limited)


search_service = ProductSearchService()


# ---------------------------------------------------------------------------
# API-endpointit
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check")
def health_check() -> dict:
    """
    Yksinkertainen health-check – vastaava löytyy usein tuotantopalveluista.
    """
    return {"status": "ok"}


@app.get(
    "/products",
    response_model=SearchResponse,
    summary="Hae tuotteita hakusanan perusteella",
)
def search_products(
    q: str = Query(..., description="Hakusana, esim. 'usb'"),
    max_results: int = Query(50, ge=1, le=200),
    only_in_stock: bool = Query(False, description="Palauta vain varastossa olevat"),
):
    """
    HTTP-rajapinta tuotehakuun.

    Tässä käytetään query-parametreja (q, max_results, only_in_stock),
    mutta saman voisi toteuttaa myös POST:na ja käyttää ProductQuery-
    runkomallia sellaisenaan.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Hakusana ei voi olla tyhjä")

    query = ProductQuery(
        search=q,
        max_results=max_results,
        only_in_stock=only_in_stock,
    )

    result = search_service.search_products(query)
    return result
