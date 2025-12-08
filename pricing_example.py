# -*- coding: utf-8 -*-
# Business Logic: Pricing Rules
# Copyright (c) 2025 Jan Sarivuo

"""
Keskitetty hinnoittelulogiikka (Business Logic Layer).

Tämä moduuli vastaa katteiden ja verojen laskennasta.
Eriyttämällä logiikan tänne, varmistamme että:
1. Kaikki toimittajaintegraatiot laskevat hinnat samalla kaavalla.
2. ALV-muutokset tarvitsee tehdä vain yhteen paikkaan.
"""

from decimal import Decimal

class PricingEngine:
    # Määritellään verokanta (25,5 %)
    # Tämän muuttaminen päivittää hinnat koko järjestelmässä
    VAT_RATE = 0.255

    @staticmethod
    def calculate_gross_price(net_price: float) -> float:
        """
        Laskee verollisen ulosmyyntihinnan verottomasta sisäänostohinnasta.
        Tähän voisi lisätä myös valuuttamuunnokset tai kateprosentit.
        """
        if net_price is None:
            return 0.0
        
        # Lasketaan hinta ja pyöristetään 2 desimaaliin
        # (Nettohinta * 1.255)
        return round(net_price * (1 + PricingEngine.VAT_RATE), 2)

    @staticmethod
    def get_margin_for_category(category: str) -> float:
        """
        Esimerkki: Palauttaa tavoitekatteen kategorialle.
        (Tätä voidaan laajentaa tulevaisuudessa)
        """
        if "cable" in category.lower():
            return 1.40 # 40% kate kaapeleille
        return 1.25 # 25% peruskate