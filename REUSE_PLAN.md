# Plano de reaproveitamento

## Princípio

Reutilize **contratos e capacidades**, não caminhos internos. O novo projeto não deve importar dezenas de arquivos por `sys.path` sem controle.

## Advanced_Btc_Bot

| Capacidade | Reaproveitamento | Ajuste necessário |
|---|---:|---|
| Tribunal de agentes | Alto | trocar prompts e esquema de saída por `InvestmentDecision` |
| Risk manager | Médio/alto | remover premissas de cripto 24/7; adicionar liquidez B3, gaps e concentração setorial |
| News fetcher | Médio | manter infraestrutura; refazer fontes, normalização e licenças |
| Observabilidade/logs | Alto | preservar trace IDs, custo, latência e erros |
| Cache/retry/rate-limit | Alto | reutilizar quase integralmente |
| Execução de ordens | Baixo inicialmente | começar somente com paper portfolio/recomendação |
| Indicadores intraday | Baixo | usar apenas como timing complementar |
| MiroFish | Alto como serviço | sidecar HTTP, sem cópia de código |

## b3_screener

| Capacidade | Reaproveitamento | Ajuste necessário |
|---|---:|---|
| Universo de ativos | Alto | exportar com schema e timestamp |
| Fundamentos | Alto | versionar fonte, período e qualidade de cada campo |
| FIIs | Alto | separar tipos, vacância, prazo de contratos e concentração |
| Cotações | Médio | não tratar dados gratuitos como fonte única de produção |
| Scrapers | Médio/baixo | encapsular, respeitar ToS, detectar mudança de HTML |
| `data.js` cache | Apenas migração | converter para JSON/Parquet; não importar arquivo JS gigante em Python |

## Estratégia recomendada

1. Criar um `export_data.js` estável dentro do `b3_screener`.
2. Gerar `universe.json` com `schema_version`, `generated_at`, `source` e `records`.
3. Ler esse arquivo pelo `B3ScreenerJsonBridge`.
4. Para o legado Python, configurar módulos e funções via `.env`.
5. Após estabilizar, extrair capacidades comuns para um pacote separado `advanced_bot_shared`.

## Não fazer

- copiar o diretório inteiro `logic/`;
- manter dois risk managers divergentes;
- chamar scrapers diretamente de agentes LLM;
- deixar o LLM montar SQL, ticker ou peso sem validação determinística;
- misturar recomendação e execução automática na primeira versão.
