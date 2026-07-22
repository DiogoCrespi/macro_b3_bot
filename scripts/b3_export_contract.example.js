// Adapt this file inside the existing b3_screener.
// It intentionally does not depend on the unknown internal shape of data.js.
const fs = require('fs');
const path = require('path');

function normalizeRecord(item) {
  return {
    ticker: String(item.ticker ?? item.symbol ?? '').toUpperCase(),
    asset_class: item.asset_class ?? item.type ?? 'stock',
    price: Number(item.price ?? item.regularMarketPrice ?? 0),
    avg_daily_volume_brl: Number(item.avg_daily_volume_brl ?? item.liquidity ?? 0),
    sector: item.sector ?? null,
    pe: item.pe ?? item.pl ?? null,
    pvp: item.pvp ?? null,
    roe: item.roe ?? null,
    roic: item.roic ?? null,
    dividend_yield: item.dividend_yield ?? item.dy ?? null,
  };
}

function exportUniverse(records, outputPath) {
  const payload = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    source: 'b3_screener',
    records: records.map(normalizeRecord).filter((item) => item.ticker && item.price > 0),
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(payload, null, 2), 'utf8');
}

module.exports = { normalizeRecord, exportUniverse };
