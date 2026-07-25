# Checklist de migração

## Fase 0 — inventário

- [ ] Rodar `macro-b3 discover-reuse --write-manifest`.
- [ ] Verificar licenças de todos os módulos reaproveitados.
- [ ] Identificar I/O, dependências globais e side effects.
- [ ] Congelar testes atuais dos módulos candidatos.

## Fase 1 — dados e auditoria

- [ ] Criar export versionado do `b3_screener`.
- [ ] Integrar BCB, CVM e documentos de RI.
- [ ] Implementar DuckDB com snapshots imutáveis.
- [ ] Implementar proveniência por campo.

## Fase 2 — eventos e causalidade

- [ ] Deduplicação de notícias/eventos.
- [ ] Extração estruturada de claims.
- [ ] Grafo evento -> variável -> setor -> empresa.
- [ ] Base de exposições empresariais por receita, custo e geografia.

## Fase 3 — MiroFish

- [x] Subir MiroFish como sidecar em ambiente de pesquisa.
- [x] Gerar seed pack com fontes e hipóteses.
- [x] Transformar relatório compatível em `ScenarioSet` estruturado; schema incompatível gera zero hipóteses.
- [x] Validar consistência, binding point-in-time e contradições.

## Fase 4 — decisão

- [ ] Integrar tribunal legado por adapter.
- [ ] Criar agente cético independente.
- [ ] Implementar política de score/veto.
- [ ] Paper portfolio com custos, impostos e slippage.

## Fase 5 — validação

- [ ] Backtest walk-forward por data de publicação.
- [ ] Avaliar precision@k, hit rate, retorno relativo, drawdown e turnover.
- [ ] Calibrar probabilidades (Brier score/reliability curve).
- [ ] Testar ablação: sem MiroFish, sem notícias, sem técnico, sem tribunal.

## Fase 6 — produção

- [ ] Alertas somente em mudança material.
- [ ] Dashboard de teses ativas e invalidadas.
- [ ] Aprovação humana autenticada antes de qualquer ordem.
- [ ] Kill switch e limite diário/semanal de exposição.
- [ ] Deployment reproduzível, secrets externos, health checks e rollback.
- [ ] Backup/restauração testados e escrita concorrente segura.

## Estado atual

As fases 0–4 possuem componentes implementados, mas a Fase 5 ainda não foi homologada
e a Fase 6 não está concluída. O sistema deve permanecer em pesquisa/staging até que
os critérios de [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) sejam atendidos.
