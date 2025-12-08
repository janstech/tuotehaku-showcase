# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Supplier XML ETL Script
# Copyright (c) 2025 Jan Sarivuo
#
# TÄMÄ SKRIPTI ON OSA PORTFOLIO-DEMOA.
#
# Toiminta:
# 1. Noutaa suuren tuotekatalogin (XML) toimittajan rajapinnasta.
# 2. Konvertoi XML-datan JSON-muotoon ja tallentaa välitiedoston (cache/debug).
# 3. Lukee JSON-datan, normalisoi kentät ja ajaa "Upsert"-operaation MySQL-kantaan.

__author__ = "Jan Sarivuo"
__version__ = "1.0.0"

import os
import json
import time
import logging
import requests
import xmltodict
import mysql.connector
from pathlib import Path
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# 1. KONFIGURAATIO
# -----------------------------------------------------------------------------

# Määritetään hakemistot
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True) # Luodaan data-kansio, jos puuttuu

# Ladataan ympäristömuuttujat
load_dotenv(BASE_DIR / ".env")

# Tietokanta-asetukset
DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASS"),
    'database': os.getenv("DB_NAME")
}

# Toimittajan API-asetukset (Anonymisoitu)
# Tuotannossa nämä arvot tulevat CI/CD-putken salaisuuksista tai Vaultista.
SUPPLIER_API_URL = os.getenv("SUPPLIER_XML_URL", "https://api.supplier-example.com/feed")
SUPPLIER_API_KEY = os.getenv("SUPPLIER_API_KEY")
SUPPLIER_CUST_ID = os.getenv("SUPPLIER_CUSTOMER_ID")

# Tunnisteet tietokannassa
SUPPLIER_ID = 1
SUPPLIER_NAME = "GlobalWholesale" # Geneerinen nimi demolle

# Tiedostopolku väliaikaiselle datalle
LOCAL_CACHE_FILE = DATA_DIR / "feed_supplier_a.json"

# Logitus
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("XMLImporter")

# ALV-kanta (käytetään bruttohinnan laskentaan)
VAT_RATE = 0.255 

# -----------------------------------------------------------------------------
# 2. EXTRACT (Datan haku)
# -----------------------------------------------------------------------------

def fetch_supplier_data(retries=3, delay=30):
    """
    Hakee toimittajan XML-feedin.
    
    Ominaisuudet:
    - Automaattinen uudelleenyritys (Retry Logic) verkkovirheille.
    - Muuntaa XML:n JSON:ksi heti latauksen jälkeen helpompaa käsittelyä varten.
    - Tallentaa datan levylle. Tämä on tärkeää, jotta parserointia voi testata
      ilman, että raskasta API-hakua tarvitsee toistaa.
    """
    
    # Rakennetaan URL dynaamisesti parametreilla
    params = {
        "database": "item",
        "customerid": SUPPLIER_CUST_ID,
        "apikey": SUPPLIER_API_KEY,
        "filetype": "extended",
        "language": "fi"
    }
    
    log.info(f"Fetching data from {SUPPLIER_NAME} API...")

    for attempt in range(1, retries + 1):
        try:
            # Timeout on tärkeä, jotta skripti ei jää roikkumaan ikuisesti
            resp = requests.get(SUPPLIER_API_URL, params=params, timeout=300)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                log.error("All fetch attempts failed.")
                raise

    size_mb = len(resp.content) / (1024 * 1024)
    log.info(f"Download complete. Size: {size_mb:.2f} MB")

    # Muunnetaan XML -> Python Dict -> JSON
    # xmltodict on tehokas kirjasto XML:n litistämiseen
    try:
        data_dict = xmltodict.parse(resp.text)
        with open(LOCAL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)
        log.info(f"Data converted and saved to {LOCAL_CACHE_FILE}")
    except Exception as e:
        log.error(f"XML parsing failed: {e}")
        raise

# -----------------------------------------------------------------------------
# 3. TRANSFORM & LOAD (Datan käsittely ja tallennus)
# -----------------------------------------------------------------------------

def _safe_get(data, *keys):
    """
    Apufunktio syvällä olevien arvojen hakemiseen turvallisesti
    XML-rakenteesta, joka voi olla sekava.
    """
    curr = data
    for k in keys:
        if isinstance(curr, dict):
            curr = curr.get(k)
        else:
            return None
    return curr

def process_data_to_db():
    """
    Lukee paikallisen JSON-tiedoston ja päivittää MySQL-tietokannan.
    
    Strategia:
    - Batch Processing: Commitoidaan 5000 rivin välein muistin säästämiseksi.
    - Upsert: Käytetään ON DUPLICATE KEY UPDATE -lausetta.
    - Dirty Data Handling: XML-feedeissä on usein puuttuvia kenttiä tai
      listat muuttuvat objekteiksi, jos on vain yksi item. Tämä funktio
      normalisoi nämä poikkeukset.
    """
    if not LOCAL_CACHE_FILE.exists():
        log.error("Local data file missing. Run fetch first.")
        return

    with open(LOCAL_CACHE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Navigoidaan XML-rakenteen syövereihin
    # Huom: Rakenne riippuu toimittajan XML-skeemasta
    products_list = _safe_get(data, "ns0:PriceList", "Products", "Product")
    
    if not products_list:
        log.warning("No products found in the data structure.")
        return

    # Varmistetaan että meillä on lista, vaikka tuotteita olisi vain yksi
    if isinstance(products_list, dict):
        products_list = [products_list]

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # DEMO-VALINTA: Poistetaanko vanhat?
    # Tuotannossa yleensä merkitään "active=0", mutta tässä
    # yksinkertaisuuden vuoksi nollataan tämän toimittajan data.
    log.info(f"Clearing old data for supplier {SUPPLIER_ID}...")
    cursor.execute("DELETE FROM products WHERE supplier_id = %s", (SUPPLIER_ID,))

    inserted_count = 0
    skipped_count = 0
    BATCH_SIZE = 5000 

    log.info(f"Starting processing of {len(products_list)} items...")

    for row in products_list:
        try:
            # --- 3a. Normalisointi (Data Cleaning) ---
            
            # Brändi ja nimi
            brand = (row.get("Brand") or "").strip()
            
            # Nimi-logiikka: XML:ssä on usein monta nimikenttää.
            # Otetaan paras saatavilla oleva.
            descs = row.get("Descriptions", {})
            name = (
                _safe_get(descs, "ProductName", "#text") or 
                _safe_get(descs, "ProductNameWeb", "#text") or 
                f"{brand} Product"
            )

            # EAN / ID
            # XML-listojen käsittely: joskus 'Identifiers' on lista, joskus dict
            ids = row.get("Identifiers", {})
            ean = _safe_get(ids, "Barcode", "#text")
            if not ean:
                ean = str(ids.get("ItemNumber") or "")

            # Hinta ja Saldo
            try:
                net_price = float(_safe_get(row, "Prices", "NetPrice", "#text") or 0)
                gross_price = round(net_price * (1 + VAT_RATE), 2)
                stock = int(float(_safe_get(row, "Inventory", "OnHand") or 0))
            except (ValueError, TypeError):
                net_price = 0.0
                gross_price = 0.0
                stock = 0

            # Kategoria (Otetaan hierarkian viimeinen taso)
            cats = _safe_get(row, "Categories", "Category")
            category_name = "Uncategorized"
            if isinstance(cats, list) and cats:
                category_name = cats[-1].get("#text")
            elif isinstance(cats, dict):
                category_name = cats.get("#text")

            # Linkki & Kuva
            link = _safe_get(descs, "ProductUrl", "#text") or "#"
            image = None
            
            # Kuvan etsintä on usein monimutkaista XML:ssä
            assets = _safe_get(row, "Assets", "Asset")
            if isinstance(assets, list):
                for asset in assets:
                    if asset.get("Type") == "primary_picture":
                        image = _safe_get(asset, "Value", "#text") or asset.get("Value")
                        break
            elif isinstance(assets, dict) and assets.get("Type") == "primary_picture":
                image = _safe_get(assets, "Value", "#text")

            # --- 3b. Tietokantaan vienti (Upsert) ---
            
            sql = """
                INSERT INTO products
                (supplier_id, supplier_name, name, brand, ean, category, 
                 price, price_inc, stock, link, image, raw_data, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    price = VALUES(price),
                    stock = VALUES(stock),
                    updated_at = NOW()
            """
            
            # Tallennetaan alkuperäinen rivi JSON:ina debuggausta varten (raw_data)
            raw_json = json.dumps(row, ensure_ascii=False, default=str)
            
            vals = (
                SUPPLIER_ID, SUPPLIER_NAME, name, brand, ean, category_name,
                net_price, gross_price, stock, link, image, raw_json
            )
            
            cursor.execute(sql, vals)
            inserted_count += 1

            # Commit erissä
            if inserted_count % BATCH_SIZE == 0:
                conn.commit()
                log.info(f"Processed {inserted_count} rows...")

        except Exception as e:
            skipped_count += 1
            # Logitetaan vain debug-tasolla, ettei loki täyty virheistä
            # log.debug(f"Row skipped due to error: {e}")
            continue

    conn.commit()
    cursor.close()
    conn.close()
    
    log.info(f"ETL Job Finished. Inserted: {inserted_count}, Skipped/Error: {skipped_count}")

# -----------------------------------------------------------------------------
# 4. MAIN
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    start_time = time.time()
    
    try:
        fetch_supplier_data()
        process_data_to_db()
    except Exception as e:
        log.critical(f"Critical failure in importer: {e}")
        exit(1)
        
    duration = time.time() - start_time
    log.info(f"Script completed in {duration:.2f} seconds.")
