# Macro B3 Intelligence Bot

Bot de inteligência macro orientado a eventos para ações, FIIs, ETFs e BDRs negociados na B3.

O sistema não tenta prever preços diretamente a partir de notícias. Ele executa a cadeia:

`evidência -> evento -> cenário -> mecanismo causal -> exposição setorial/empresarial -> valuation -> timing -> risco -> recomendação`

## Decisão arquitetural

- **Python 3.11/3.12** como orquestrador principal.
- **MiroFish como sidecar HTTP opcional**, sem copiar o código-fonte para este projeto.
- **b3_screener como provedor de universo/fundamentos**, preferencialmente por export JSON versionado.
- **Advanced_Btc_Bot como biblioteca legada configurável**, reaproveitando tribunal, risco, notícias e observabilidade somente quando os contratos forem compatíveis.
- **DuckDB** para snapshots, eventos, previsões e auditoria.
- **Execução event-driven**: a coleta pode ser frequente; recomendações só são emitidas quando há mudança material.

## O que já funciona no scaffold

- modelos de domínio e política de recomendação;
- score causal e filtros de segurança;
- bridge para JSON exportado pelo `b3_screener`;
- adapter configurável para módulos legados do `Advanced_Btc_Bot`;
- cliente HTTP configurável para MiroFish;
- inventário automático dos dois repositórios locais;
- pipeline demonstrativo e testes unitários;
- scripts de bootstrap para Windows.

## Instalação

```powershell
cd C:\Nestjs\Advanced_Btc_Bot\macro_b3_bot
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
macro-b3 discover-reuse
macro-b3 demo
pytest
```

## Integração local

Ajuste no `.env`:

```env
ADVANCED_BTC_BOT_ROOT=C:\Nestjs\Advanced_Btc_Bot\Advanced_Btc_Bot
B3_SCREENER_ROOT=C:\Nestjs\Advanced_Btc_Bot\b3_screener
B3_SCREENER_EXPORT=C:\Nestjs\Advanced_Btc_Bot\b3_screener\exports\universe.json
MIROFISH_BASE_URL=http://localhost:5001
```

Depois execute:

```powershell
macro-b3 discover-reuse --write-manifest
macro-b3 validate-config
macro-b3 demo
```

## 📊 Estudo de Eventos (Sprint 3B.1)

O bot conta com um pipeline unificado e robusto de estudo de eventos para notícias e fatos relevantes da CVM (IPE):

```powershell
# Executa o pipeline completo (consolidação, mapeamento, download de preços, CAR, bootstrap + Benjamini-Hochberg)
python src/macro_b3_bot/interfaces/cli.py run-event-study --bootstrap-iterations 10000 --seed 42
```

### Premissas e Metodologia:
* **Consolidação por Evento Econômico**: As claims são agrupadas por `(cvm_code, data_anuncio)`, tratando múltiplos anúncios/versões no mesmo dia como um único evento corporativo, evitando a pseudo-replicação estatística.
* **Calendário B3 Fiel**: Resolução de timezones para manter os pregões de segunda-feira ativos (evitando o bug de deslocamento de data UTC).
* **Mapeamento Point-in-Time**: O ticker Guararapes é processado localmente como `GUAR3` e mapeado dinamicamente para `RIAA3.SA` apenas no momento da consulta de cotações históricas no Yahoo Finance.
* **Correção Múltipla**: Aplicação do procedimento de False Discovery Rate de Benjamini-Hochberg a 10% nos p-values das reações acumuladas (CAR 5 dias).

## Central Rule

O bot deve poder responder **“nenhuma compra agora”**. Um sistema obrigado a indicar ativos em toda execução é um gerador de narrativas, não um sistema de decisão.
