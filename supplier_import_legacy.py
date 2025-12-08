# -*- coding: utf-8 -*-
# Supplier B Data Import (SFTP -> ZIP -> CSV -> MySQL)
# Copyright (c) 2025 Jan Sarivuo

"""
ETL-skripti: Supplier B (CSV-pohjainen).

Tämä skripti demonstroi kykyä käsitellä "legacy"-tyyppisiä integraatioita,
joissa data siirretään tiedostoina SFTP:n yli eikä modernin API:n kautta.

Toiminta:
1. Yhdistää SFTP-palvelimelle ja lataa ZIP-pakatun hinnaston sekä varastosaldot.
2. Purkaa ZIP-paketin ja lukee suuret CSV-tiedostot muistiin tehokkaasti (Pandas).
3. Yhdistää (merge) hinnastorivit ja varastosaldot Pythonissa.
4. Laskee myyntihinnat ja päivittää MySQL-tietokannan.
"""

import os
import json
import zipfile
import paramiko
import pandas as pd
import mysql.connector
import math
import logging
from pathlib import Path
from dotenv import load_dotenv

# Tuodaan keskitetty hinnoittelulogiikka
from pricing_example import PricingEngine

# ---------- CONFIG ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# Tietokanta
DB_CONFIG = {
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME"),
    "charset": "utf8mb4",
    "use_unicode": True,
}

# Toimittajan asetukset (Anonymisoitu)
SUPPLIER_ID = 2
SUPPLIER_NAME = "Supplier B (Nordic)"
SFTP_HOST = os.getenv("SUPPLIER_B_SFTP_HOST")
SFTP_USER = os.getenv("SUPPLIER_B_SFTP_USER")
SFTP_PASS = os.getenv("SUPPLIER_B_SFTP_PASS")

# Logitus
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [Supplier-B] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("ImportSupplierB")


# ---------- HELPERS ----------
def safe_json(data):
    """
    Muodostaa turvallisen JSON-merkkijonon raakadatasta.
    Käsittelee NaN/Inf arvot, jotka usein rikkovat JSON-serialisoinnin.
    """
    def clean_value(v):
        if isinstance(v, float):
            if math.isinf(v) or math.isnan(v):
                return None
        return v
    
    clean_dict = {k: clean_value(v) for k, v in data.items()}
    return json.dumps(clean_dict, ensure_ascii=False, default=str)


def download_files_via_sftp():
    """
    Noutaa tiedostot SFTP-palvelimelta.
    """
    if not SFTP_HOST:
        log.error("SFTP config missing.")
        return None, None

    log.info(f"Connecting to SFTP: {SFTP_HOST}")
    
    try:
        transport = paramiko.Transport((SFTP_HOST, 22))
        transport.connect(username=SFTP_USER, password=SFTP_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)

        local_zip = DATA_DIR / "supplier_b_pricelist.zip"
        local_stock = DATA_DIR / "supplier_b_stock.txt"

        # Tiedostonimet ovat usein toimittajakohtaisia vakioita
        log.info("Downloading pricelist archive...")
        sftp.get("pricelist-11.txt.zip", str(local_zip))

        log.info("Downloading stock file...")
        sftp.get("stock.txt", str(local_stock))

        sftp.close()
        transport.close()
        return local_zip, local_stock
    
    except Exception as e:
        log.error(f"SFTP transfer failed: {e}")
        raise


def extract_zip_archive(local_zip):
    """Purkaa ZIP-paketin."""
    with zipfile.ZipFile(local_zip, "r") as zf:
        zf.extractall(DATA_DIR)
        extracted_files = zf.namelist()
    
    log.info(f"Extracted: {extracted_files}")
    return [DATA_DIR / f for f in extracted_files]


def connect_db():
    return mysql.connector.connect(**DB_CONFIG)


# ---------- MAIN PIPELINE ----------
def run_import_pipeline():
    """
    Pääprosessi: ETL (Extract, Transform, Load).
    """
    
    # 1. EXTRACT
    try:
        local_zip, local_stock = download_files_via_sftp()
    except Exception:
        log.critical("Failed to download files. Aborting.")
        return

    files = extract_zip_archive(local_zip)
    
    # Oletetaan, että ensimmäinen tiedosto zipissä on hinnasto
    price_file = files[0]
    
    log.info("Loading CSV data into Pandas (this may take memory)...")
    
    # Pandas on tehokas suurten CSV-tiedostojen lukemisessa
    # dtype=str varmistaa, että EAN-koodit, joissa on nollia alussa, eivät katkea
    df_price = pd.read_csv(price_file, sep="\t", dtype=str, keep_default_na=False)
    df_stock = pd.read_csv(local_stock, sep="\t", dtype=str, keep_default_na=False)

    log.info(f"Pricelist rows: {len(df_price)}")
    
    # Varastodatan mäppäys
    # Normalisoidaan sarakeotsikot, koska ne voivat vaihdella toimittajan päässä
    stock_pid_col = "ProductID" if "ProductID" in df_stock.columns else "ProductId"
    stock_qty_col = "AvailableQuantity" if "AvailableQuantity" in df_stock.columns else "StockQty"

    # Luodaan nopea hakutaulu (Hash Map) varastosaldoille: ProductID -> Qty
    stock_map = dict(zip(df_stock[stock_pid_col], df_stock[stock_qty_col]))

    # 2. LOAD PREPARATION
    conn = connect_db()
    cursor = conn.cursor()

    # Poistetaan vanha data (Full Refresh strategy)
    cursor.execute("DELETE FROM products WHERE supplier_id=%s", (SUPPLIER_ID,))
    conn.commit()
    log.info(f"Old data cleared for supplier {SUPPLIER_ID}")

    inserted, skipped = 0, 0

    # 3. TRANSFORM & LOAD ITERATION
    # Iterrows on hidas, mutta tässä tapauksessa tarvitaan rivikohtaista logiikkaa
    for idx, row in df_price.iterrows():
        try:
            # Hinnasto on "likainen": puolipisteellä eroteltu string yhdessä sarakkeessa
            # Tämä on tyypillistä legacy-datalle.
            raw_line = str(row.iloc[0])
            cols = raw_line.split(";")
            
            if len(cols) < 7:
                skipped += 1
                continue

            # Parsitaan sarakkeet (indeksit perustuvat toimittajan speksiin)
            brand = cols[0].strip()
            product_id = cols[1].strip()
            category_raw = cols[2].strip()
            name = cols[3].strip()
            
            # Numeroiden parsinta varovasti
            try:
                base_stock = int(float(cols[4] or 0))
                price = float(cols[5] or 0)
            except ValueError:
                base_stock = 0
                price = 0.0
                
            ean = cols[6].strip()

            if not product_id and not ean:
                skipped += 1
                continue

            # Yhdistetään varastotieto toisesta tiedostosta (Data Enrichment)
            # Jos stock-tiedostosta löytyy saldo, käytetään sitä, muuten hinnaston omaa
            external_stock = stock_map.get(product_id)
            if external_stock:
                final_stock = int(float(external_stock))
            else:
                final_stock = base_stock

            # Business Logic: Lasketaan ALV-hinta keskitetyllä laskurilla
            price_inc = PricingEngine.calculate_gross_price(price)

            # Linkin generointi (perustuu toimittajan julkiseen verkkokauppaan)
            product_link = f"https://shop.supplier-b.com/detail?id={product_id}"

            # SQL INSERT
            cursor.execute("""
                INSERT INTO products
                  (supplier_id, supplier, name, brand, category, category_raw,
                   price, price_inc, stock, link, image, ean, raw_data)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  price=VALUES(price),
                  stock=VALUES(stock),
                  updated_at=CURRENT_TIMESTAMP
            """, (
                SUPPLIER_ID,
                SUPPLIER_NAME,
                name,
                brand,
                category_raw, # Käytetään raakaa kategoriaa sellaisenaan
                category_raw,
                price,
                price_inc,
                final_stock,
                product_link,
                None, # Ei kuvaa tässä feedissä
                ean if ean else None,
                safe_json(row.to_dict()), # Tallennetaan raaka data JSON:ina
            ))
            inserted += 1

            if inserted % 5000 == 0:
                conn.commit()
                log.info(f"{inserted} rows committed...")

        except Exception as e:
            # Yksittäinen virheellinen rivi ei saa kaataa koko prosessia
            skipped += 1
            # log.debug(f"Skipped row {idx}: {e}")

    conn.commit()
    cursor.close()
    conn.close()

    log.info(f"Pipeline finished. Inserted: {inserted}, Skipped: {skipped}")


if __name__ == "__main__":
    run_import_pipeline()