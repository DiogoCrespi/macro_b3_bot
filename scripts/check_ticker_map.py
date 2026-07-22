import duckdb

c = duckdb.connect("data/audit.duckdb")

print(f"company_ticker_map total: {c.execute('SELECT COUNT(*) FROM company_ticker_map').fetchone()[0]}")
r = c.execute("SELECT cvm_code, ticker, cnpj, mapping_source, confidence FROM company_ticker_map ORDER BY confidence DESC LIMIT 20").fetchall()
for x in r:
    print(x)

print("\n--- Checking cvm_codes from events ---")
event_codes = [
    "23531","26050","4669","22608","509280","19763","20010","23000",
    "25160","27049","25780","26166","21016","22810","23167","24821"
]
for code in event_codes:
    rows = c.execute(
        "SELECT ticker, mapping_source, confidence FROM company_ticker_map WHERE cvm_code=?",
        [code]
    ).fetchall()
    print(f"  cvm_code={code}: {rows if rows else 'NO MAPPING'}")

c.close()
