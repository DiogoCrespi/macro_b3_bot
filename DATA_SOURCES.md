# Estratégia de dados

## Prioridade 1 — fontes primárias

- Brasil: BCB/SGS e Expectativas, CVM (ITR, DFP, IPE, FRE, fundos), IBGE/SIDRA, Tesouro Nacional e documentos de RI.
- Exterior: FRED/ALFRED, EIA, NOAA/NCEI, USDA, SEC, bancos centrais e organismos multilaterais.
- Dados corporativos: fatos relevantes, releases, apresentações, teleconferências e formulários regulatórios.

## Prioridade 2 — preços e fundamentos agregados

- `b3_screener` como camada de consolidação local;
- provedores gratuitos somente com reconciliação e campos de proveniência;
- fonte paga passa a ser necessária antes de execução automática ou capital relevante.

## Prioridade 3 — narrativas

- RSS e notícias licenciadas;
- GDELT ou agregadores para descoberta, não verdade final;
- YouTube/podcasts para localizar teses e variáveis, nunca para confirmar fatos isoladamente.

## Qualidade mínima por registro

```json
{
  "source_id": "bcb_sgs_11",
  "source_tier": 1,
  "observed_at": "2026-07-21T12:00:00Z",
  "published_at": "2026-07-21T10:00:00Z",
  "effective_date": "2026-07-21",
  "revision": 0,
  "value": 0.0,
  "unit": "% a.a.",
  "url": "...",
  "checksum": "sha256:..."
}
```

## Regras

- armazenar vintage/revisão para evitar look-ahead bias;
- diferenciar data de publicação, referência e coleta;
- nunca substituir silenciosamente valor ausente por zero;
- manter fonte por campo, não apenas por ativo;
- detectar stale data e outliers;
- guardar o documento bruto e a extração estruturada.
