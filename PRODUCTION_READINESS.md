
# Avaliação de prontidão para produção

**Data da avaliação:** 25/07/2026
**Estado avaliado:** commit `2b7931f`
**Última suíte local:** 339 testes aprovados em 80,65 s

## Veredito

O projeto **não está pronto para produção como sistema de decisão de investimentos**.

Está apto somente para um **piloto interno de pesquisa/staging**, com as seguintes
restrições:

- saídas limitadas a `WATCH` e `NO_ACTION`;
- `BUY`, seleção operacional e ordens desabilitados;
- MiroFish tratado como gerador de hipóteses, nunca como evidência final;
- valuation e DCF bloqueados quando os gates de prontidão não forem satisfeitos;
- revisão e auditoria obrigatórias antes de qualquer uso decisório.

## Evidências do estado atual

- A cadeia macro → setor → empresa → impacto financeiro → decisão está implementada.
- O sidecar MiroFish real possui execução HTTP, polling, persistência do relatório bruto,
  checksum e extração estruturada com proveniência.
- O binding 5B não fabrica caminhos: no evento avaliado, registrou
  `REJECTED_MACRO_EVENT_NO_ACTIVE_CANDIDATE` e não criou `SectorImpactCandidate` inexistente.
- O gate de decisão bloqueia hipóteses não suportadas, sem binding ou com contradição.
- O último resultado operacional real permanece `NO_ACTION`.
- O CI publicado foi aprovado em Python 3.11 e 3.12; a suíte local atual tem 338 testes.

Esses fatos demonstram integridade do fluxo experimental, não validade econômica para
produção.

## Bloqueios de produção

### Evidência e modelo

- Não há quantidade suficiente de hipóteses MiroFish verificadas, causalmente vinculadas
  e sem contradições.
- O evento real testado não possui candidato setorial ativo.
- Ainda faltam backtests walk-forward, calibração fora da amostra, precision@k,
  reliability/Brier, drawdown, turnover e testes de ablação.
- O bridge financeiro ainda contém sensibilidades estruturais, regressões in-sample e
  FCF normalizado estatisticamente; não é base para DCF.

### Infraestrutura

- O armazenamento principal é DuckDB local. A escrita concorrente entre múltiplos
  processos precisa ser serializada ou migrada para um banco transacional.
- Faltam deployment reproduzível, migrations versionadas, backup e teste de restauração,
  retries, dead-letter, circuit breaker, health checks e rollback.
- O CI ainda não cobre build de imagem, vulnerabilidades, integração real com o sidecar,
  concorrência, restauração ou smoke test pós-deploy.

### Segurança e governança

- O exemplo de configuração ainda é de desenvolvimento (`APP_ENV=development`).
- Segredos precisam sair de arquivos `.env` e ser geridos por secrets externos.
- A chave Zep exposta durante o desenvolvimento deve ser revogada e rotacionada.
- Faltam autenticação, autorização, RBAC, auditoria imutável, kill switch e limites de
  exposição.
- Antes de qualquer ordem real, deve existir aprovação humana autenticada e segregada.

### Operação

- Não há SLOs, alertas, dashboard de teses, monitoramento de dados atrasados ou runbook
  de incidentes.
- Ainda não existe paper portfolio calibrado com custos, slippage e reconciliação.

## Classificação por ambiente

| Ambiente               | Permitido  | Condição                                             |
| ---------------------- | ---------- | ------------------------------------------------------ |
| Desenvolvimento        | Sim        | Dados e credenciais locais, sem decisão operacional   |
| Staging/piloto interno | Sim        | Dados PIT, logs, backup e`BUY` desabilitado          |
| Produção de pesquisa | Ainda não | Requer operação, segurança e monitoramento mínimos |
| Produção decisória  | Não       | Requer validação estatística e financeira           |
| Execução de ordens   | Não       | Requer governança, aprovação e controles de risco   |

## Critérios para liberar produção de pesquisa

1. Containerização reproduzível do orquestrador e do sidecar.
2. Banco com escrita segura, migrations, backup e restauração testada.
3. Secrets externos e rotação de credenciais.
4. Health checks, métricas, alertas, retries e circuit breaker.
5. Scheduler com lock, idempotência e observabilidade de cada run.
6. Testes de integração/e2e e smoke test pós-deploy.
7. Runbook de falhas e rollback documentado.

## Critérios adicionais para decisão financeira

1. Hipóteses `SUPPORTED` e `BOUND`, com consistência temporal e sem contradição.
2. Pelo menos cinco replays históricos por bridge, com validação fora da amostra.
3. FCF normalizado marcado `VALUATION_READY`, não apenas proxy estatístico.
4. Dados de mercado point-in-time e valuation reproduzível.
5. Paper portfolio com custos, slippage, limites e reconciliação.
6. Aprovação humana autenticada antes de qualquer ação externa.

## Próximo roteiro de implementação

1. Fechar a infraestrutura operacional de staging.
2. Implementar observabilidade, backup, restore e execução idempotente.
3. Executar validação histórica dos bridges existentes.
4. Implementar o gate formal de prontidão para valuation.
5. Somente então avaliar paper portfolio e, posteriormente, qualquer integração de ordens.

Até que esses critérios sejam atendidos, o comportamento correto do sistema é bloquear
ou retornar `NO_ACTION`, nunca fabricar uma tese, valuation ou ordem.

---

Implementações:

validação semântica perfeita do MiroFish
ablação histórica completa com/sem MiroFish
replay diário integral de 2024–2026
generalização para centenas de empresas
eliminação de toda regra específica por ticker
autenticação e hardening do sidecar
otimização de pesos
produção de WATCH obrigatória
integração com corretora
BUY ou execução real
