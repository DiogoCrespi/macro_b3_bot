# Revisão manual de exposições — ativos solicitados

Data de corte: `2026-07-26`  
Política: `UNKNOWN_WHEN_NOT_EXPLICIT` — nenhum percentual foi inferido apenas pelo setor.

| Ativo | Tipo | Canais macro identificados | Resultado da busca |
|---|---|---|---|
| TRXF11 | FII renda urbana | juros, inflação, crédito de locatários, varejo | Relatório gerencial oficial localizado; percentuais de indexação/concentração ainda `UNKNOWN` |
| HGLG11 | FII logística | juros, inflação, demanda logística, vacância | Relatório de junho localizado; ocupação/indexadores aguardam extração do documento original |
| CPTS11 | FII crédito híbrido | juros, inflação, spread, crédito imobiliário | Relatório localizado; mix CDI/IPCA ainda `UNKNOWN` |
| GGRC11 | FII industrial/logística | juros, inflação, crédito de locatários, demanda industrial | Relatório oficial de junho localizado; contratos atípicos e prazo aguardam extração |
| BBAS3 | banco | Selic, ciclo de crédito, risco-país, agrocrédito, inadimplência | RI oficial localizado; composição de ativos sensíveis ainda `UNKNOWN` |
| SAPR4 | saneamento regulado | inflação, juros, tarifa, capex, hidrologia | RI oficial e página tarifária localizados; elasticidades ainda `UNKNOWN` |
| KNCR11 | FII de crédito CDI | juros, spread, crédito imobiliário | **78,6% do PL em CRI CDI; CDI+2,06%; prazo médio 4,1 anos; 12,6% LCI; 8,7% caixa** |
| GARE11 | FII híbrido | juros, inflação, crédito de locatários, varejo, transações | Relatório de junho localizado; concentração/indexação aguardam extração |
| QQQI11 | ETF Nasdaq-100 covered call | USD/BRL, tecnologia EUA, volatilidade implícita, juros EUA | **Mandato Nasdaq-100 + estratégia de calls cobertas confirmado; hedge cambial em BRL `UNKNOWN`** |
| XPML11 | FII shopping centers | juros, inflação, varejo, vendas, vacância | Relatório de junho localizado; vendas/ocupação aguardam extração |

## Uso no sistema

O JSON correspondente é um catálogo de evidências manuais. Ele não cria
`CompanyImpactCandidate` automaticamente. Valores `UNKNOWN` continuam bloqueados
para cálculo; somente fatos com documento e divulgação explícita podem entrar no
motor causal.

Fontes principais: páginas oficiais de relatórios TRXF11, GGRC11, Kinea/KNCR11,
Banco do Brasil RI, Sanepar RI e Buena Vista/QQQI11; arquivos de relatórios FNET/BrFiis
foram usados apenas para localizar os documentos de HGLG11, CPTS11, GARE11 e XPML11.
