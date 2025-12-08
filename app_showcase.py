# -*- coding: utf-8 -*-
# Product Search API (FastAPI)
# Copyright (c) 2025 Jan Sarivuo
# Portfolio Demo Version

"""
Tämä on FastAPI-pohjainen taustajärjestelmä (backend) tukkukaupan tuotehakua varten.
Järjestelmä yhdistää usean eri toimittajan saldot ja hinnat yhteen hakunäkymään.

Ominaisuudet:
- Nopea haku MySQL-tietokannasta (Fulltext + fuzzy logic)
- Älykäs hakulogiikka (Strict vs. Loose mode)
- Datan päivitysrajapinta (triggeröi ulkoiset ETL-skriptit)
- Tietoturvallinen konfiguraatio (.env)
"""

__author__ = "Jan Sarivuo"
__version__ = "1.0.0"

import os
import re
import sys
import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime

import mysql.connector
from mysql.connector import pooling
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv, dotenv_values

# -----------------------------------------------------------------------------
# 1. KONFIGURAATIO JA YMPÄRISTÖMUUTTUJAT
# -----------------------------------------------------------------------------

# Määritetään projektin juurihakemisto
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

# Ladataan ympäristömuuttujat
load_dotenv(ENV_PATH)

# Tietokanta-asetukset
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "user")
DB_PASS = os.getenv("DB_PASS", "password")
DB_NAME = os.getenv("DB_NAME", "wholesale_db")

# Loggausasetukset
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ProductAPI")

# CORS-asetukset (sallitaan määritellyt front-endit)
ALLOW_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()]

def get_reload_token() -> str:
    """
    Hakee reload-tokenin suoraan .env-tiedostosta levyltä.
    
    Arkkitehtuurivalinta:
    Luetaan tiedosto joka kerta erikseen, jotta tokenin voi vaihtaa 
    tuotannossa ilman, että koko API-palvelua tarvitsee käynnistää uudelleen.
    """
    try:
        cfg = dotenv_values(ENV_PATH)
        return (cfg.get("RELOAD_TOKEN") or "").strip()
    except Exception:
        return (os.getenv("RELOAD_TOKEN") or "").strip()

# -----------------------------------------------------------------------------
# 2. SOVELLUKSEN ALUSTUS
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Wholesale Product Search API",
    description="High-performance search engine for aggregated supplier data.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# 3. TIETOMALLIT (Pydantic)
# -----------------------------------------------------------------------------

class ProductItem(BaseModel):
    """Yksittäisen tuotteen tiedot hakutuloksissa."""
    provider: str
    name: str
    brand: Optional[str] = None
    price: float
    price_inc_vat: float
    stock: int = 0
    link: Optional[str] = None
    image: Optional[str] = None
    ean: Optional[str] = None

class SearchResponse(BaseModel):
    """API-vastauksen rakenne hakukyselyille."""
    query: str
    products: List[ProductItem]
    has_more: bool  # Kertoo frontendille, onko lisää sivuja saatavilla
    limit: int
    offset: int

# -----------------------------------------------------------------------------
# 4. TIETOKANTA JA APUFUNKTIOT
# -----------------------------------------------------------------------------

def get_db_connection():
    """
    Luo yhteyden tietokantaan.
    Tuotannossa tässä käytettäisiin Connection Poolia suorituskyvyn parantamiseksi.
    """
    if not all([DB_HOST, DB_USER, DB_PASS, DB_NAME]):
        # Demo-moodi: jos tunnuksia ei ole, heitetään virhe (tai voitaisiin palauttaa mock-objekti)
        raise RuntimeError("Database configuration missing from .env")
    
    return mysql.connector.connect(
        host=DB_HOST, 
        port=DB_PORT, 
        user=DB_USER, 
        password=DB_PASS, 
        database=DB_NAME
    )

def normalize_string(text: str) -> str:
    """
    Puhdistaa hakusanan tietoturvalliseksi ja SQL-yhteensopivaksi.
    Sallii vain alfanumeeriset merkit ja yleisimmät erikoismerkit.
    """
    text = str(text or "").lower()
    # Poistetaan kaikki merkit paitsi a-z, 0-9, skandit, piste ja viiva
    text = re.sub(r"[^a-z0-9äöå\.\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def split_search_terms(raw_query: str) -> Tuple[List[str], List[str]]:
    """
    Älykäs hakusanan pilkkominen.
    Jakaa syötteen 'normaaleihin' termeihin ja 'erikoistermeihin' (kuten tuotekoodit).
    """
    normalized = normalize_string(raw_query)
    tokens = [t for t in normalized.split() if len(t) >= 2]
    return tokens, []

# --- SQL Query Builders (Estää SQL Injectionit käyttämällä parametrisoituja kyselyitä) ---

def build_strict_search_query(where_clauses: List[str], tokens: List[str]) -> Tuple[str, List[str]]:
    """
    Rakentaa 'Strict Mode' -kyselyn (AND-logiikka).
    Kaikkien hakusanojen on löydyttävä joko tuotteen nimestä tai brändistä.
    """
    sql_parts = []
    params = []
    
    for token in tokens:
        sql_parts.append("(LOWER(name) LIKE %s OR LOWER(brand) LIKE %s)")
        wildcard_token = f"%{token}%"
        params.extend([wildcard_token, wildcard_token])
    
    # Yhdistetään pohjaehdot (esim. stock > 0) ja hakusanat
    full_where = " AND ".join(where_clauses + sql_parts) if (where_clauses or sql_parts) else "1=1"
    
    sql = f"""
        SELECT supplier_id, supplier_name, name, brand, price, price_vat, stock, ean, link, image
        FROM products
        WHERE {full_where}
        ORDER BY price ASC
        LIMIT %s OFFSET %s
    """
    return sql, params

def build_fuzzy_search_query(where_clauses: List[str], terms: List[str]) -> Tuple[str, List[str]]:
    """
    Rakentaa 'Fuzzy Mode' -kyselyn (FULLTEXT / Boolean Mode).
    Käytetään, jos tarkka haku ei tuota tuloksia. Hyödyntää MySQL:n FULLTEXT-indeksejä.
    """
    params = []
    parts = list(where_clauses)
    
    # Rakennetaan FULLTEXT-haku: "+termi1* +termi2*"
    if terms:
        boolean_query = " ".join(f"+{t}*" for t in terms)
        parts.append("MATCH(name, brand) AGAINST (%s IN BOOLEAN MODE)")
        params.append(boolean_query)
        
    full_where = " AND ".join(parts) if parts else "1=1"
    
    sql = f"""
        SELECT supplier_id, supplier_name, name, brand, price, price_vat, stock, ean, link, image
        FROM products
        WHERE {full_where}
        ORDER BY price ASC
        LIMIT %s OFFSET %s
    """
    return sql, params

# -----------------------------------------------------------------------------
# 5. API RAJAPINNAT (ENDPOINTS)
# -----------------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Kevyt endpoint kuormituksenjakajalle (Load Balancer) tilan tarkistukseen."""
    return {"status": "ok", "service": "product-search-api"}

@app.get("/search", response_model=SearchResponse)
async def search_products(
    q: str = Query(..., min_length=2, description="Hakusana"),
    in_stock: bool = Query(True, description="Näytä vain varastossa olevat tuotteet"),
    strict_mode: bool = Query(True, description="Käytä tiukkaa hakulogiikkaa (True) tai sumeaa hakua (False)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Pääasiallinen tuotehaku.
    
    Logiikka:
    1. Yritetään ensin tiukalla haulla (AND).
    2. Jos tuloksia on vähän ja 'strict_mode' ei ole pakotettu, voidaan laajentaa hakua.
    3. Palauttaa 'has_more' -lipun sivutusta varten (optimointi: haetaan limit + 1).
    """
    raw_query = q.strip()
    conn = get_db_connection()
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # 1. Määritetään suodattimet
        where_conditions = []
        if in_stock:
            where_conditions.append("stock > 0")
            
        tokens, _ = split_search_terms(raw_query)
        if not tokens:
            raise HTTPException(status_code=400, detail="Hakusana liian lyhyt tai epäkelpo")

        # 2. Valitaan hakustrategia
        if strict_mode:
            sql, params = build_strict_search_query(where_conditions, tokens)
        else:
            sql, params = build_fuzzy_search_query(where_conditions, tokens)

        # 3. Suoritetaan kysely (haetaan yksi ylimääräinen rivi sivutustiedon tarkistamiseksi)
        fetch_limit = limit + 1
        cursor.execute(sql, params + [fetch_limit, offset])
        rows = cursor.fetchall()
        
        # 4. Tarkistetaan onko lisää sivuja
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit] # Leikataan ylimääräinen rivi pois näytettävästä datasta

        if not rows and offset == 0:
             # Tähän voisi lisätä ns. 'Fallback'-haun (esim. poistetaan stock-filtteri tai löysennetään ehtoja)
             pass

        # 5. Muotoillaan vastaus
        # Anonymisoidaan toimittaja-ID:t mappauksella demoa varten
        provider_map = {1: "Global Wholesale Ltd", 2: "Nordic Parts Oy"}
        
        results = []
        for r in rows:
            results.append(ProductItem(
                provider=provider_map.get(r.get("supplier_id"), "Unknown Supplier"),
                name=r.get("name") or "N/A",
                brand=r.get("brand"),
                price=float(r.get("price") or 0),
                price_inc_vat=float(r.get("price_vat") or 0),
                stock=int(r.get("stock") or 0),
                link=r.get("link"),
                image=r.get("image"),
                ean=r.get("ean")
            ))

        return SearchResponse(
            query=raw_query, 
            products=results, 
            has_more=has_more, 
            limit=limit, 
            offset=offset
        )

    except Exception as e:
        log.error(f"Search failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error during search processing")
    finally:
        if conn.is_connected():
            conn.close()

# -----------------------------------------------------------------------------
# 6. HALLINTARAJAPINNAT (ADMIN / ETL)
# -----------------------------------------------------------------------------

@app.post("/reload-data")
async def trigger_data_reload(authorization: Optional[str] = Header(None)):
    """
    Turvallinen endpoint datan päivitykselle.
    Käynnistää taustalla Python-skriptit, jotka hakevat datan toimittajien API:sta.
    
    Vaatii: Authorization: Bearer <TOKEN>
    """
    expected_token = get_reload_token()
    
    # Yksinkertainen Bearer-token tarkistus
    if not authorization or not authorization.startswith("Bearer ") or authorization.split(" ")[1] != expected_token:
        log.warning("Unauthorized reload attempt")
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    try:
        # Käytetään nykyistä Python-tulkkia
        py_exec = sys.executable
        
        # Määritellään ajettavat ETL-skriptit (nimetty geneerisesti demoa varten)
        importer_scripts = [
            BASE_DIR / "scripts" / "import_supplier_global.py",
            BASE_DIR / "scripts" / "import_supplier_nordic.py"
        ]

        results = {}
        
        # Ajetaan skriptit synkronisesti (tuotannossa tämä tehtäisiin Celery/Redis-jonolla)
        for script in importer_scripts:
            script_path = str(script.resolve())
            
            # Subprocess on eristetty tapa ajaa legacy-skriptejä
            process = subprocess.run(
                [py_exec, script_path], 
                capture_output=True, 
                text=True
            )
            
            script_name = script.name
            results[script_name] = {
                "return_code": process.returncode,
                "log_tail": process.stdout[-200:] if process.stdout else "No output"
            }
            
            log.info(f"Ran import script {script_name} with code {process.returncode}")

        return {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "details": results
        }

    except Exception as e:
        log.exception("Data reload process failed")
        raise HTTPException(status_code=500, detail=str(e))