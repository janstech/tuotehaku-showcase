# Wholesale Product Search Engine (Showcase)

Tämä repositorio sisältää otteita tuotantokäytössä olevasta B2B-tuotehakujärjestelmästä. Järjestelmä yhdistää usean tukkutoimittajan (miljoonia rivejä) dataa yhteen nopeaan hakunäkymään.

**Huomio:** *Koodi on anonymisoitu ja yksinkertaistettu demo-tarkoituksiin. API-avaimet, oikeat katelaskentakaavat ja asiakastiedot on poistettu.*

## 🏗 Arkkitehtuuri

Järjestelmä on rakennettu mikropalvelu-hengessä, jossa raskas datan käsittely on eriytetty WordPress-frontista.

* **Frontend:** WordPress + Custom Plugin (PHP/JS)
* **Backend API:** Python FastAPI (Async)
* **Database:** MySQL (Fulltext search optimized)
* **ETL & Integrations:** Python-skriptit (CSV/XML/API)

## Tiedostorakenne

| Tiedosto | Kuvaus |
| :--- | :--- |
| `backend/app_example.py` | **FastAPI Search API.** Hakulogiikka, välimuisti ja reititys. |
| `backend/supplier_update_example.py` | **ETL Pipeline.** Datan nouto toimittajilta ja normalisointi tietokantaan. |
| `backend/pricing_example.py` | **Business Logic.** Hintojen ja katteiden laskenta asiakasryhmittäin. |
| `wordpress/tuotehaku_endpoint.php` | **WP REST Proxy.** Yhdistää WordPressin Python-backendiin turvallisesti. |

## Keskeiset ratkaisut

* **Suorituskyky:** Raskas haku on siirretty pois PHP:ltä nopeaan Python-backendiin.
* **Hakutarkkuus:** Hybridihaku (Strict match + Fuzzy logic) varmistaa, että oikeat tuotteet löytyvät myös kirjoitusvirheillä.
* **Skalautuvuus:** Toimittajaintegraatiot ovat modulaarisia; uuden tukun lisääminen vaatii vain uuden `Adapter`-luokan.

---
*Copyright (c) 2025 Jan Sarivuo*
