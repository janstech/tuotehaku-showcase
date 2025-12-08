"""
Tuotehaku – backend-esimerkki (showcase-versio)

Tämä on yksinkertaistettu versio tuotantokäytössä olleesta
FastAPI-pohjaisesta tuotehakupalvelusta. Koodi demonstroi mm.:

- FastAPI + Pydantic
- SQLAlchemy ORM -malli ja sessiot
- Yksinkertainen välimuisti (TTLCache)
- Hakuehtojen koostaminen dynaamisesti

"""

from typing import List, Optional

import os

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from cachetools import TTLCache

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    select,
    and_,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# --------------------------------------------------------------------
# Konfiguraatio
# --------------------------------------------------------------------

# Oikea URL tulee lukemalla esim. ympäristömuuttujasta tai .env-tiedostosta.

DATABASE_URL = os.getenv(
    "DEMO_DATABASE_URL",
    "postgresql://user:password@localhost:5432/demo_products",
)

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()

# --------------------------------------------------------------------
# Tietokantamalli (typistetty)
# --------------------------------------------------------------------


class Product(Base):
    """
    Esimerkkituote. Oikeassa järjestelmässä sarakkeita oli enemmän
    (esim. toimittajat, varastosaldot, EAN-koodit, jne.).
    """

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, index=True)          # tuotenumero
    name = Column(String, index=True)         # nimi
    brand = Column(String, index=True)        # valmistaja/brand
    category = Column(String, index=True)     # pääluokka
    price = Column(Float)                     # myyntihinta


# --------------------------------------------------------------------
# Pydantic-skeemat
# --------------------------------------------------------------------


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None

    class Config:
        orm_mode = True


# --------------------------------------------------------------------
# Yhteinen DB-sessio / dependency
# --------------------------------------------------------------------


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------
# Välimuisti – yksinkertainen TTLCache
# --------------------------------------------------------------------

# Avain: hakusana + valitut filtterit (string)
# Arvo: list[ProductOut]
search_cache: TTLCache = TTLCache(maxsize=1_000, ttl=60)  # 60 s


def cache_key(
    query: str,
    brand: Optional[str],
    category: Optional[str],
) -> str:
    """
    Muodostetaan yksinkertainen avain välimuistia varten.
    """
    return f"q={query}|brand={brand}|cat={category}"


# --------------------------------------------------------------------
# FastAPI-sovellus
# --------------------------------------------------------------------

app = FastAPI(
    title="Tuotehaku – demo backend",
    description=(
        "Yksinkertaistettu esimerkki tuotantokäytössä olleesta "
        "tuotehakupalvelusta (FastAPI + SQLAlchemy)."
    ),
    version="1.0.0",
)


# --------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health_check() -> dict:
    """
    Yksinkertainen health-check, jota esim. valvonta voi kysellä.
    """
    try:
        with engine.connect() as conn:
            conn.execute(select(1))
    except Exception as exc:  # pragma: no cover - demo
        raise HTTPException(status_code=500, detail=str(exc))

    return {"status": "ok"}


# --------------------------------------------------------------------
# Hakurajapinta
# --------------------------------------------------------------------


@app.get("/search", response_model=List[ProductOut], tags=["search"])
def search_products(
    query: str = Query(..., min_length=1, description="Vapaa tekstihaku, esim. 'näyttö 27'"),
    brand: Optional[str] = Query(None, description="Rajaa tiettyyn brändiin"),
    category: Optional[str] = Query(None, description="Rajaa kategoriaan"),
    limit: int = Query(20, ge=1, le=200, description="Palautettavien rivien määrä"),
    db: Session = Depends(get_db),
) -> List[ProductOut]:
    """
    Tuotehaku:

    - Hakee tuotteita nimen, tuotenumeroiden ym. perusteella.
    - Tukee lisäsuodattimia (brand & category).
    - Hyödyntää yksinkertaista 60 s TTL-välimuistia toistuviin hakuihin.
    """
    key = cache_key(query=query, brand=brand, category=category)
    if key in search_cache:
        return search_cache[key]

    # Rakennetaan dynaamisesti WHERE-ehdot
    filters = []

    # Yksinkertainen case-insensitive LIKE-haku
    like_pattern = f"%{query.lower()}%"
    filters.append(Product.name.ilike(like_pattern) | Product.sku.ilike(like_pattern))

    if brand:
        filters.append(Product.brand == brand)

    if category:
        filters.append(Product.category == category)

    stmt = (
        select(Product)
        .where(and_(*filters))
        .order_by(Product.name.asc())
        .limit(limit)
    )

    products = db.execute(stmt).scalars().all()
    result = [ProductOut.from_orm(p) for p in products]

    # Talletetaan välimuistiin
    search_cache[key] = result

    return result
