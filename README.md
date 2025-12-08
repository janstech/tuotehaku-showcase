# Wholesale Product Search Engine (Showcase)

Tämä repositorio sisältää otteita tuotantokäytössä olevasta B2B-tuotehakujärjestelmästä. Järjestelmä yhdistää usean tukkutoimittajan (miljoonia rivejä) dataa yhteen nopeaan hakunäkymään.

⚠️ **Huomio:** *Koodi on anonymisoitu ja yksinkertaistettu demo-tarkoituksiin. API-avaimet, oikeat katelaskentakaavat ja asiakastiedot on poistettu.*

## Arkkitehtuuri

Järjestelmä on rakennettu mikropalvelu-hengessä, jossa raskas datan käsittely on eriytetty WordPress-frontista.

* **Frontend:** WordPress + Custom Plugin (PHP/JS)
* **Backend API:** Python FastAPI (Async)
* **Database:** MySQL (Fulltext search optimized)
* **ETL & Integrations:** Python-skriptit (CSV/XML/API/SFTP)

## Tiedostorakenne

### Backend (Python)
| Tiedosto | Kuvaus |
| :--- | :--- |
| `backend/app_example.py` | **FastAPI Search API.** Hakulogiikka, välimuisti ja reititys. |
| `backend/supplier_update_example.py` | **XML API Importer.** Datan haku modernista REST/XML-rajapinnasta ja normalisointi. |
| `backend/supplier_import_legacy.py` | **CSV/SFTP Importer.** Suurten datamassojen käsittely Pandas-kirjastolla (Legacy-integraatiot). |
| `backend/pricing_example.py` | **Business Logic.** Keskitetty hintojen, verojen ja katteiden laskentalogiikka. |
| `backend/requirements.txt` | **Dependencies.** Projektin vaatimat kirjastot (mm. FastAPI, Pandas, MySQL-connector). |

### Frontend (PHP/WordPress)
| Tiedosto | Kuvaus |
| :--- | :--- |
| `wordpress/tuotehaku_endpoint.php` | **WP REST Proxy.** Custom Plugin, joka yhdistää WordPressin Python-backendiin turvallisesti. Sisältää myös Vanilla JS -käyttöliittymän. |

## Keskeiset ratkaisut

* **Suorituskyky:** Raskas haku ja datan prosessointi on siirretty pois PHP:ltä nopeaan Python-backendiin.
* **Tietoturva:** WordPress toimii proxyna, joten sisäverkon API-osoitteet tai avaimet eivät paljastu selaimelle.
* **Hakutarkkuus:** Hybridihaku (Strict match + Fuzzy logic) varmistaa, että oikeat tuotteet löytyvät myös kirjoitusvirheillä.
* **Skalautuvuus:** Toimittajaintegraatiot ovat modulaarisia; uuden tukun lisääminen vaatii vain uuden `Adapter`-skriptin.

---
*Copyright (c) 2025 Jan Sarivuo*
