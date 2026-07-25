# Política de dados do baseline P0

O diretório `data/` contém classes diferentes de artefato. Elas não podem ser
misturadas em uma análise sem indicar a proveniência.

| Classe | Local | Uso |
|---|---|---|
| Auditoria/fixture controlada | `data/audits/`, `data/mirofish_seeds/` | Testes, homologação e reprodução; não é evidência upstream de produção |
| Aquisição upstream | `data/raw/` e manifests de aquisição | Dados externos; exigem fonte, `collected_at`, `available_at`, versão e checksum |
| Snapshots locais | `data/*.duckdb*` | Estado local não versionado; deve ser reconstruído ou restaurado por backup |
| Cache | `data/market_info_cache.json` | Não é fonte primária nem evidência point-in-time |

O manifesto [baseline_reproducible.json](audits/baseline_reproducible.json) registra o
hash dos arquivos versionados e sua classe. Fixtures controladas não podem ser
apresentadas como execução upstream real.

## Regra para novos artefatos

Todo arquivo novo em `data/` deve declarar sua classe e conter, quando aplicável:

- fonte e endpoint;
- `available_at` e `collected_at`;
- identificador de versão;
- checksum do conteúdo bruto;
- `as_of_timestamp` usado na seleção.
