import pharmacy_catalog


def test_one_product_can_resolve_from_each_of_its_barcodes(monkeypatch):
    monkeypatch.setattr(
        pharmacy_catalog,
        "catalog_dataframe",
        lambda: pharmacy_catalog.pd.DataFrame(
            [{"ProductName": "TEST PRODUCT", "Barcodes": "12345678|87654321"}]
        ),
    )
    pharmacy_catalog.barcode_index.cache_clear()
    try:
        first = pharmacy_catalog.lookup_pharmacy_product("12345678")
        second = pharmacy_catalog.lookup_pharmacy_product("87654321")
        assert first["product_name"] == "TEST PRODUCT"
        assert second["product_name"] == "TEST PRODUCT"
        assert first["barcodes"] == "12345678 | 87654321"
    finally:
        pharmacy_catalog.barcode_index.cache_clear()


def test_real_avene_and_lierac_products_exist_in_catalog():
    pharmacy_catalog.catalog_dataframe.cache_clear()
    pharmacy_catalog.barcode_index.cache_clear()
    avene = pharmacy_catalog.lookup_pharmacy_product("3282770148763")
    lierac = pharmacy_catalog.lookup_pharmacy_product("3701436933524")
    assert avene["product_name"] == "AVENE CICALFATE EMULSION POST-ACTE TATTOO 40ML"
    assert lierac["product_name"] == "LIERAC COFFRET LIFT CR JOUR + RECH 25"
