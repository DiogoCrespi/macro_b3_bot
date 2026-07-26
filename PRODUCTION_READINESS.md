# Avaliação de prontidão para produção

**Data da avaliação:** 26/07/2026
**Estado avaliado:** working tree após correção de recuperação de relatório e glossário de domínio (P3)
**Última suíte local:** 361 testes aprovados em 81,29 s em Python 3.13

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
- O CI publicado foi aprovado em Python 3.11 e 3.12; a suíte local atual tem 361 testes.

### P3 — última execução real (25/07/2026)

- O prompt enviado ao sidecar agora contém um glossário obrigatório e identificadores
  canônicos: IPCA brasileiro, ITR como Informações Trimestrais da CVM, evento,
  claim, estado setorial e documento-fonte.
- A suíte focada do parser/grounding passou: **31 testes**; Ruff passou.
- A execução real `639abc3fa1f4c7c714c2e2ad6d06e17a13a5eabe2af5d6a5a6849471b177573a`
  chegou a gerar e persistir um relatório completo no sidecar, mas o endpoint de
  status devolveu HTTP 500 durante a leitura concorrente do arquivo de progresso.
  O resultado foi corretamente classificado como
  `FAILED_INCOMPLETE_SERVICE_RUN`; não foram criadas hipóteses.
- Foi implementada recuperação somente quando `/api/report/list` confirma um
  relatório `COMPLETED`; nenhum relatório ou hipótese é sintetizado localmente.
- `SUPPORTED + BOUND + CONSISTENT` permanece **pendente** até uma nova execução
  real concluir e o relatório respeitar o glossário. A falha atual é operacional
  do sidecar/status, não autorização para relaxar o grounding semântico.

### P3 — recuperação e extração compatível (25/07/2026)

- O sidecar concluiu posteriormente a simulação `sim_7896f408497b` e expôs o
  relatório `report_a2e86c0545f5` em `/api/report/list`. O comando
  `scripts/recover_mirofish_completed_run.py` recuperou somente esse relatório
  `COMPLETED`, persistiu bytes/checksum e gravou uma execução `SUCCESS` no DuckDB.
- A extração LLM estrita passou após uma segunda tentativa auditável: `trigger`
  é string, cada cenário possui `report_excerpt` literal e o schema foi validado.
  Não houve cenário local nem preenchimento de confiança.
- A revisão delegada passou o grounding de fonte para `PARTIALLY_SUPPORTED` com
  IPCA/BR, claim e documento PIT preservados. Isso ainda não equivale a revisão
  humana nem a `SUPPORTED` operacional.
- O binding real foi executado e persistido: `temporal_consistency_status=CONSISTENT`,
  mas `binding_status=REJECTED_MACRO_EVENT_NO_ACTIVE_CANDIDATE`, pois o único
  candidato macro disponível (`0c0e7a1f8b0035011ae94d28`) está rejeitado e não há
  `sector_impact_candidate` ativo para o evento. Nenhum caminho causal foi criado.
- Estado atual: relatório semanticamente compatível **PASS**; `SUPPORTED + BOUND`
  **PENDENTE por ausência de candidato causal upstream**. Suíte completa: **353
  testes aprovados**; Ruff: **PASS**.
- A avaliação setorial no banco PIT canônico `audit.duckdb`, run
  `sector_p3_real_20260726`, persistiu 469 candidatos e 15 snapshots. Uma
  execução posterior com o evento real `BCB_SGS_11_2026-06-17` confirmou quatro
  setores ativos no seed, mas o sidecar excedeu `TIMEOUT_PREPARE` ao preparar os
  agentes. Ela foi interrompida sem relatório ou hipótese; deve ser retomada com
  o mesmo seed content-addressed ou por recuperação do `simulation_id` exposto.

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

## Plano mestre de implementação

Este arquivo é o caminho oficial de implementação. Cada alteração futura deve:

1. referenciar uma fase e um item deste plano;
2. atualizar o status e a evidência nesta seção;
3. registrar testes, artefatos e bloqueios;
4. atualizar a data e o commit avaliado;
5. não avançar uma fase enquanto seus critérios de aceite não estiverem comprovados.

### Fase P0 — Higiene e baseline reprodutível

**Status:** `CONCLUÍDA`
**Objetivo:** garantir que qualquer execução possa ser reproduzida e auditada.

- [X] Suíte local completa executada (`342 passed` em Python 3.12).
- [X] Ruff e `git diff --check` aprovados.
- [X] Bytecodes rastreados removidos do Git.
- [X] Fixar dependências Python 3.12 em `requirements-py312.lock` e versão em `.python-version`.
- [X] Criar `data/audits/baseline_reproducible.json` com commit, configuração e checksums.
- [X] Separar dados controlados e upstream em `data/README.md` e no manifesto.

**Saída:** `baseline_reproducible.json`, `requirements-py312.lock`, `.python-version` e
`data/README.md`.

### Fase P1 — Staging operacional seguro

**Status:** `EM ANDAMENTO`
**Objetivo:** executar o orquestrador e o sidecar como serviço controlado, sem ordens.

- [X] Containerizar o orquestrador e declarar o sidecar no Compose.
- [X] Criar configuração de staging distinta de `.env` de desenvolvimento.
- [X] Fixar a imagem do sidecar por digest imutável no ambiente de staging.
- [X] Declarar secrets externos via Compose; rotação da chave Zep ainda pendente.
- [X] Implementar health checks, timeout, retry básico e circuit breaker.
- [X] Implementar cancelamento cooperativo do workflow após falha/timeout, via `/api/simulation/stop`.
- [X] Implementar wrapper de scheduler com lock, idempotência e status por execução.
- [ ] Integrar o wrapper a um scheduler externo com comando e `run_id` point-in-time.
- [X] Definir volume persistente e backup verificado do DuckDB.
- [X] Testar cópia restaurável em ambiente temporário.
- [X] Definir retenção e limpeza automática de backups (`--keep`, padrão 7).

**Evidência atual:** `docker build` aprovado, workflow GHCR concluído com sucesso, imagem
MiroFish publicada e fixada por digest multi-arquitetura (`docker buildx imagetools inspect`),
proveniência registrada em `data/audits/mirofish_sidecar_image.json`, `docker compose config` aprovado, health
check do container aprovado, backup com `restore_check=PASS` e segunda execução retornando
`STAGING_RUN_ALREADY_SUCCEEDED`, circuit breaker aberto após falhas transitórias e
`restore_check=PASS`; o worker foi executado localmente e dentro da imagem Docker com
`worker-ok`. O aceite final permanece bloqueado por registro persistente do scheduler
e rotação de secrets. O cancelamento cooperativo foi
implementado para falhas/timeouts; cancelamento iniciado por operador ainda requer comando
operacional autenticado.

O `docker pull` local do sidecar excedeu o timeout de 3 minutos por tamanho da imagem;
isso não invalida o manifesto publicado, mas o smoke test pós-pull ainda não foi aprovado.

**Secrets:** o `.env` do sidecar contém uma chave Zep de desenvolvimento e o arquivo
`.secrets/zep_api_key` de staging ainda não existe. A chave não foi copiada nem versionada;
é necessário revogá-la no provedor Zep, criar uma nova credencial e materializá-la somente
no secret store local/CI antes de executar o Compose.

**Scheduler:** adaptadores reais estão em `scripts/register_staging_task.ps1` e
`scripts/invoke_staging_job.ps1`. O registro foi validado com `-WhatIf` e o invocador
foi executado com `run_id` explícito e retorno `scheduler-invoker-ok`; a tarefa persistente
não foi criada porque horário, identidade operacional e política de execução ainda não
foram definidos.

### Fase P2 — Observabilidade e governança

**Status:** `EM ANDAMENTO`
**Objetivo:** tornar falhas e decisões auditáveis por uma pessoa responsável.

- [X] Autenticação por token hash configurado externamente, sem persistência do segredo.
- [ ] RBAC separando operador, revisor e administrador.
- [X] Núcleo de log append-only com hash chain para revisão/binding/decisão.
- [X] Métricas de ingestão, atraso PIT, falhas do sidecar, hipóteses e bloqueios em snapshots JSON.
- [X] Avaliação determinística de alertas para atraso PIT, conflitos, falhas e baixa aprovação.
- [X] Dashboard estático read-only de métricas gerado por `scripts/render_observability_dashboard.py`.
- [X] Kill switch fail-closed com permissão administrativa e motivo obrigatório.

**Evidência P2 inicial:** `application/governance.py`, `application/observability.py`,
`docs/STAGING_RUNBOOK.md` e testes validam RBAC mínimo, autenticação por hash, ledger
append-only, kill switch, snapshots de métricas, alertas determinísticos e dashboard
read-only. SSO/RBAC corporativo, entrega externa de alertas e publicação do dashboard ainda
estão pendentes.

### Fase P3 — Grounding e binding econômico real

**Status:** `CONCLUÍDA COM RESSALVAS`
**Objetivo:** obter hipóteses realmente compatíveis com eventos e estados PIT reais.

- [X] Remover templates locais de cenário.
- [X] Persistir relatório bruto, checksum e extração estruturada.
- [X] Rejeitar incompatibilidade semântica IPCA/global e ITR/tecnologia.
- [X] Separar revisão delegada de revisão humana.
- [X] Persistir revisão, validação e binding append-only.
- [X] Proibir matching por número ou substring isolada.
- [X] Executar um relatório MiroFish semanticamente compatível com evento brasileiro real.
- [X] Preservar `source_document_ids` no seed e validar sua disponibilidade PIT; a execução sem hipótese mantém zero vínculos.
- [X] Obter `SUPPORTED + BOUND + CONSISTENT` sem conflito para o piloto Selic.
- [X] Demonstrar candidato setorial ativo no mesmo corte.

**Aceite:** pelo menos um caso real passa todos os gates sem alterar o payload canônico.
O caso abaixo foi aceito com proveniência e estado de revisão explicitamente registrados.

**Execução P3 real:** `run_id=39b8886c50eb97053d0601c39590a9290c2eda3ee38845c4636d124d9dbaff03`,
evento `rel_bcb_ipca_202607`, corte `2026-07-22T23:59:59+00:00`. Sidecar, LLM, preparação,
simulação e relatório concluíram; o relatório foi rejeitado como `FAILED_UNSUPPORTED_REPORT_SCHEMA`
e gerou zero hipóteses porque continha inflação global e ITR como tecnologia. Nenhum binding,
WATCH ou decisão foi criado.

**Correção concluída:** o prompt/seed agora carrega glossário obrigatório de domínio
(`Selic=taxa básica brasileira`, `IPCA=índice nacional brasileiro`, `ITR=Informações Trimestrais CVM`),
e a validação rejeita traduções tecnológicas ou geografia incompatível. O replay abaixo demonstrou
 mudança do relatório e do estado de grounding sem recorrer a templates locais.

`DELEGATED_AI_APPROVED` é suficiente para este piloto de pesquisa conforme a política
explicitamente configurada, mas permanece distinguido de uma assinatura humana independente.

### Fase P4 — Validação histórica dos bridges

**Status:** `EM ANDAMENTO — PRIMEIRO REPLAY PERSISTIDO`
**Objetivo:** medir se os impactos financeiros têm poder explicativo fora da amostra.

- [X] Replays walk-forward por data de disponibilidade para os bridges com séries PIT.
- [X] Mínimo de cinco janelas para os bridges calculáveis; bridges sem série ficam explicitamente bloqueados.
- [~] MGLU3: dívida flutuante efetiva foi ligada a fato aprovado (`100%`, pós-hedge) e saldo
  médio observado; caixa sensível, repricing e derivativos continuam explicitamente ausentes.
- [~] SUZB3: FX e celulose com 8 janelas OOS; volume, custos cambiais e margem incremental
  continuam drivers ausentes e não são inferidos.
- [~] KLBN11: FX com 7 janelas OOS; CDI/SOFR e IPCA separados com 8 janelas OOS cada,
  mas parcela efetivamente exposta e repricing ainda não calibrados.
- [X] MAE/RMSE fora da amostra e estabilidade de sinal persistidos; bridges continuam
  não promovidos quando faltam drivers econômicos ou estabilidade.
- [X] Precision@k, hit rate, drawdown, turnover e custos calculados ou marcados como
  `NOT_EVALUABLE_NO_ALLOCATED_OUTCOMES` quando não há alocações avaliáveis.
- [X] Ablação `DETERMINISTIC_ONLY` versus `DETERMINISTIC_PLUS_MIROFISH` executada; braços
  idênticos porque não havia hipótese MiroFish `SUPPORTED+BOUND` nos cortes históricos.

**Aceite:** resultados persistidos com premissas, erro observado e intervalos; nenhum
coeficiente in-sample é promovido automaticamente a calibração validada.

**Primeiro replay P4:** `data/audits/financial_p4_historical_validation.json`,
run `financial_p4_walk_forward`, corte `2026-07-22T23:59:59+00:00`.

```text
MGLU3 NET_INTEREST_CASH_EFFECT: 12 observações; estrutural; sem OOS
SUZB3 FX_OPERATING_REVENUE:     8 janelas OOS; MAE OOS 0,1545; não promovido
KLBN11 FX_OPERATING_REVENUE:     7 janelas OOS; MAE OOS 0,0593; sinal instável; não promovido
KLBN11 CDI_SOFR_DEBT:            BLOCKED_MISSING_MACRO_SERIES
KLBN11 IPCA_DEBT:                BLOCKED_MISSING_MACRO_SERIES
```

Os resultados estão também na tabela DuckDB `historical_bridge_validation_runs`, com
`validation_id`, corte PIT, parâmetros, MAE in/out-of-sample, estabilidade de sinal,
drivers ausentes e `promotion_status=NOT_PROMOTED_TO_VALUATION`. Nenhum valor sintético
foi criado para séries ausentes. P4 permanece aberto até haver exposição efetiva de juros
da MGLU3 e séries PIT suficientes para os bridges de dívida da KLBN11.

**Recalibração P4 executada em `financial_4d3b_integrity`:** os payloads atuais foram
reconstruídos no contrato `BridgeCalibrationResult` e persistidos em
`financial_bridge_calibrations`. MGLU3 continua `STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE`
por falta de parcela efetivamente flutuante, caixa sensível, repricing e derivativos;
SUZB3 e KLBN11 continuam sem promoção por erro/estabilidade OOS e drivers ausentes.
Payloads legados não são reinterpretados como calibração válida.

**Métricas de decisão P4:** `data/audits/p4_decision_metrics_ablation.json` calcula
precision@k, hit rate, drawdown, turnover e custos a partir do replay 4G. Como os cinco
eventos históricos foram `NO_ALLOCATION`, precision@k e hit rate são `null` com status
`NOT_EVALUABLE_NO_ALLOCATED_OUTCOMES`; não são convertidos artificialmente em zero.
Drawdown, turnover e custos observados são zero. A ablação possui diferença zero e status
`NO_MIROFISH_SUPPORTED_AT_HISTORICAL_CUTOFFS`, portanto não promove o MiroFish nem altera
a política decisória.

### Fase P5 — Valuation readiness

**Status:** `EM ANDAMENTO — GATE EXECUTADO EM MODO FAIL-CLOSED`
**Objetivo:** impedir valuation artificial e liberar somente dados qualificados.

- [X] Gate formal `VALUATION_READY`/`VALUATION_BLOCKED` persistido por empresa.
- [X] Bloqueio explícito para FCF estatístico não normalizado e calibração legada incompatível.
- [X] Baselines e mercado point-in-time reproduzíveis, com corte `2026-07-26T23:59:59+00:00`.
- [X] Múltiplos observados classificados como `DESCRIPTIVE_ONLY`, nunca como fair value.
- [~] Cenários de receita, EBITDA, lucro e FCF: aguardam FCF normalizado e calibração promovida.
- [X] DCF, preço-alvo, BUY e ordens bloqueados enquanto qualquer gate falhar.

**Execução persistida:** `data/audits/valuation_p5_readiness.json`, gerado por
`scripts/run_valuation_readiness_pilot.py`. Foram avaliadas MGLU3, SUZB3, KLBN11,
RAIL3 e SLCE3. O resultado atual é fail-closed: as três primeiras têm calibração
legada incompatível com o schema atual e FCF estatístico; RAIL3 e SLCE3 não possuem
 snapshot de FCF normalizado. Após a recalibração `financial_4d3b_integrity`, os
payloads atuais são lidos sem erro de schema, mas continuam bloqueados por baixa
confiança, validação OOS insuficiente e/ou conflito macro. Portanto,
`valuation_eligible=false` e `dcf_eligible=false`
para 5/5. O artefato confirma `formal_gate_present`, `fcf_proxy_blocked`,
`no_fair_value_or_price_target` e `pit_cutoff`; nenhum valor justo ou preço-alvo foi
calculado.

Os múltiplos de mercado, quando disponíveis, são apenas descritivos e carregam
`not_a_fair_value=true` e `not_buy_eligible=true`. O script ignora payloads de
calibração históricos que não possuem o contrato P4 atual e registra
`CALIBRATION_SCHEMA_INVALID`, em vez de reinterpretá-los ou promovê-los.

O FCF normalizado permanece bloqueado de forma verificável: os documentos disponíveis
contêm CFO e capex total, mas não uma divulgação explícita de capex de manutenção nem
uma reconciliação de itens não recorrentes. O artefato registra
`MAINTENANCE_CAPEX_NOT_EXPLICITLY_DISCLOSED` e
`CFO_NON_RECURRING_COMPONENTS_NOT_RECONCILED`; o valor estatístico não é promovido a
`VALUATION_READY`.

**Evidência externa incorporada:** `scripts/ingest_issuer_adjusted_fcf.py` persistiu
`data/audits/issuer_adjusted_fcf_20260726.json` com checksums dos documentos primários.
SUZB3 possui maintenance capex de R$ 7,880 bilhões e fluxo de caixa operacional
ajustado de R$ 13,856 bilhões no release de 4T25; KLBN11 possui capex de manutenção
de R$ 3,197 bilhões e FCF ajustado LTM de R$ 1,152 bilhão no ITR 1T26. Esses valores
foram registrados como `ISSUER_DISCLOSED_ADJUSTED_FCF`, com fórmula, componentes,
documento e localização de origem. MGLU3 permanece bloqueada porque o pacote PIT
disponível não separa maintenance capex de capex total nem reconcilia CFO não recorrente.

**Aceite:** cada bloqueio informa código e evidência; nenhum preço-alvo é produzido para
empresa sem FCF e calibração aptos.

### Fase P6 — Paper portfolio e decisão controlada

**Status:** `EM ANDAMENTO — REPLAY PIT PERSISTIDO E VALIDADO`
**Objetivo:** testar decisões sem capital real.

- [X] Paper portfolio PIT executado por 34 cutoffs e 640 sessões de mercado.
- [X] Custos de transação e slippage modelados; impostos permanecem explicitamente
  não aplicados no piloto (`NOT_IMPLEMENTED`), portanto não há alegação de retorno líquido.
- [X] Reconciliação do run, snapshots e performance no DuckDB canônico `data/audit.duckdb`.
- [X] Limites de concentração, exposição e perda permanecem ativos no engine.
- [ ] Tratamento de eventos corporativos com evidência PIT para toda a série.
- [ ] Aprovação humana autenticada antes de qualquer ação externa.
- [X] Relatório de performance e benchmarks persistidos por período.
- [X] Kill switch e ordens reais permanecem bloqueados; nenhuma entrada/saída simulada
  foi autorizada porque não houve decisão PIT aprovada para alocação.

**Evidência atual:** `data/audits/p6_paper_portfolio_readiness.json`,
`data/audits/p6_allocation_eligibility.json`,
`data/audits/paper_portfolio_4g_run.json`, `data/audits/paper_portfolio_4g_ledger.json`,
`data/audits/paper_portfolio_4g_performance.json` e
`data/audits/replay_4g_end_to_end.json`. O run é reproduzível pelo script
`scripts/run_sprint4g_paper_portfolio_replay.py` usando o mesmo policy version e intervalo.

**Aceite:** ainda pendente um período de paper trading com decisões aprovadas ou uma
justificativa de cobertura para o estado `NO_ACTION`, reconciliação de eventos corporativos,
impostos quando aplicáveis e kill switch exercitado em staging. O resultado atual é
`PAPER_REPLAY_NO_ACTION`, não uma tese ou uma promessa de performance. A auditoria de
elegibilidade (`p6_allocation_eligibility.json`) confirma `0/5` decisões elegíveis. O
MGLU3 agora possui `WATCH` em modo upstream real, com setor ativo, exposição delegadamente
aprovada e canal financeiro PIT; ainda assim permanece fora da alocação por
`WAIT_FOR_CONFIRMATION`/`ELEVATED_RISK` por volatilidade realizada de 21 dias acima do
limite operacional. A frescura do evento BCB agora é `FRESH` no timing engine. As demais quatro empresas continuam bloqueadas
por upstream, conflito ou ausência de sinal. Nenhuma aprovação foi criada artificialmente.

### Fase P7 — Produção decisória e execução

**Status:** `INICIADA — AUDITORIA FAIL-CLOSED BLOQUEADA`
**Objetivo:** somente após P0–P6, avaliar produção e eventual integração de corretora.

- [X] Auditoria fail-closed de configuração e governança executada.
- [X] `BUY` e execução de ordens permanecem desabilitados por configuração e ambiente.
- [ ] Revisão de segurança e compliance.
- [ ] Segregação de credenciais de corretora.
- [ ] Sandbox de ordens e reconciliação com broker.
- [ ] Aprovação humana segregada e auditável.
- [ ] Limites pré-trade, pós-trade e kill switch independente.
- [ ] Rollback operacional e plano de incidente.

**Evidência atual:** `data/audits/p7_production_readiness.json`. A auditoria confirma
`order_execution_disabled=true`, `buy_signals_disabled=true`, governança rejeitando
operador não autenticado e zero chamadas de broker. A fase permanece bloqueada por
ausência de sandbox/reconciliação de corretora, aprovação humana segregada e entrega
externa de alertas.

**Aceite:** somente uma aprovação formal de produção pode alterar o estado para
`PRODUÇÃO DECISÓRIA`. Até lá, `BUY` e ordens continuam bloqueados.

Até que esses critérios sejam atendidos, o comportamento correto do sistema é bloquear
ou retornar `NO_ACTION`, nunca fabricar uma tese, valuation ou ordem.

## Estado do Sprint 5B.1

O fechamento de integridade do MiroFish foi implementado e testado:

- grounding controlado rejeita incompatibilidades como IPCA nacional versus inflação global;
- revisão delegada permanece distinta de revisão humana (`0,60` versus `1,00`);
- revisão, validação e binding são append-only;
- o payload canônico da hipótese não é atualizado após revisão;
- coincidência numérica ou textual não cria binding;
- evento inexistente, relatório incompleto e timestamps ausentes bloqueiam o binding;
- hipóteses não verificadas são neutras para o núcleo determinístico e aparecem apenas como warnings.

Isso fecha os defeitos de integridade do 5B para o caminho exercitado. A homologação
econômica ampla ainda depende de mais eventos, claims/documentos PIT compatíveis e replays
históricos suficientes; o piloto compatível abaixo já comprova o primeiro caso aceito.

### Replay P3 compatível — 26/07/2026

Foi executado novamente o sidecar real com o evento brasileiro `BCB_SGS_11_2026-06-17`
(`Selic Taxa Overnight`, `% a.a.`, geografia `BR`) e corte
`2026-07-22T23:59:59+00:00`. A execução terminal persistida contém:

```text
project_id:     proj_6b9fd5c98f68
graph_id:       mirofish_e960d8d31c1b4f57
simulation_id:  sim_63426c666f17
report_id:      report_fe4b2e5e8c25
status:         SUCCESS
report_checksum: 7278bf53b2fa6c6248cc92240203cde6d43f0c07cd34d419b4cdac61632f92c9
hypotheses:     2
```

O relatório bruto foi persistido em `data/raw/mirofish/reports/<checksum>.json` e a
extração estruturada registrou modelo, prompt hash, resposta bruta, checksum e reparo
determinístico do ponteiro de trecho. O reparo só escolhe um parágrafo literal do relatório;
não cria cenário nem confiança.

A avaliação setorial PIT `sector_p3_active_20260726` persistiu 15 snapshots, incluindo
135 candidatos `SECTOR_IMPACT_WATCH` hipotéticos. Para o evento acima existem candidatos
ativos em `BANCOS` e `VAREJO`, entre outros, no mesmo corte. Como as arestas permanecem
hipóteses, nenhum impacto foi promovido a `APPROVED`.

A primeira hipótese do conjunto foi revisada e vinculada com os seguintes estados:

```text
semantic_review:              SUPPORTED
reviewer_type:                DELEGATED_AI_SEMANTIC_REVIEW
review_assurance:             DELEGATED_AI_FALLBACK_EQUIVALENT_FOR_PILOT
binding_status:               BOUND
temporal_consistency_status:  CONSISTENT
contradiction_status:         NO_CONTRADICTION_DETECTED
pit_inputs_complete:          true
binding_reason:               EXACT_PIT_EVENT_CLAIM_SECTOR_PATH
```

O `hypothesis_id` original não foi atualizado: revisão, validação e binding são registros
append-only com IDs próprios. A segunda hipótese permanece `UNVERIFIED` e não participa de
qualquer decisão. A aprovação acima é delegada e explicitamente identificada; não equivale
a uma assinatura humana independente para fins regulatórios.

O serviço MiroFish foi reconstruído localmente para limitar o outline do contrato estruturado
a duas seções no modo `5A.3-mirofish-scenario-report-v1`; isso é uma otimização operacional
versionável do sidecar, não conteúdo causal hard-coded. O código do sidecar precisa permanecer
versionado no repositório próprio antes de qualquer implantação externa.

**Estado P3 após o replay:** `SUPPORTED + BOUND + CONSISTENT` demonstrado para uma hipótese
delegadamente revisada; hipótese adicional não verificada; decisão operacional ainda bloqueada
por política. Não há valuation, DCF, `BUY` ou ordens.

---

## Registro de progresso

| Data       | Commit             | Fase | Resultado                                                                                                                                                                                                                                                                                                                              | Próximo bloqueio                                                                |
| ---------- | ------------------ | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 25/07/2026 | `6e3fefa`        | P3   | 5B.1 implementado; hipótese atual rejeitada corretamente                                                                                                                                                                                                                                                                              | Caso real`SUPPORTED + BOUND`                                                   |
| 25/07/2026 | `a3dc7e1`        | P0   | 341 testes, Ruff, Python 3.12 e manifesto reproduzível aprovados                                                                                                                                                                                                                                                                      | P1: staging operacional seguro                                                   |
| 25/07/2026 | `f4eb3aa`        | P1   | Digest GHCR multi-arquitetura fixado, validador fail-closed, worker local/Docker, adaptadores Task Scheduler validados em WhatIf e invocador executado com run_id, cancelamento cooperativo, Docker build, Compose config, health check, backup/restore, lock/idempotência e circuit breaker aprovados; 345 testes, Ruff e diff check | Registro persistente do scheduler, rotação de secrets e smoke pós-pull        |
| 25/07/2026 | `a9ae27a`        | P2   | RBAC mínimo, autenticação por hash, ledger append-only, kill switch, métricas, alertas, dashboard read-only e runbook implementados e testados; 352 testes                                                                                                                                                                         | SSO/RBAC corporativo, entrega de alertas e monitoramento externo                 |
| 26/07/2026 | `P3-RUN-39b8886` | P3   | Execução real do sidecar/LLM com seed PIT, relatório bruto e checksums persistidos; incompatibilidade semântica rejeitada corretamente; zero hipóteses e zero binding                                                                                                                                                             | Relatório nativo semanticamente compatível e`SUPPORTED + BOUND + CONSISTENT` |
| 26/07/2026 | `P3-RUN-d7c115b` | P3   | Replay real com Selic brasileira: relatório terminal persistido, 2 hipóteses, candidato setorial WATCH no mesmo corte e primeira hipótese `SUPPORTED + BOUND + CONSISTENT`; segunda hipótese permanece UNVERIFIED | Versionar sidecar, executar 5C e manter decisão conservadora |
| 26/07/2026 | `financial_p4_walk_forward` | P4 | Replay PIT atualizado após ingestão BCB SGS 12/433: SUZB3 e KLBN11 FX, CDI/SOFR e IPCA com 7–8 janelas OOS; MGLU3 estrutural; nenhum bridge promovido | Determinar exposição efetiva, repricing/derivativos e completar RMSE/ablação com outcomes avaliáveis |
| 26/07/2026 | `P5-valuation-readiness-pilot-v1` | P5 | Gate formal executado para 5 empresas; 5/5 bloqueadas; múltiplos somente descritivos; zero DCF, fair value, preço-alvo, BUY ou ordens; artefato PIT persistido | Promover calibração P4 e FCF normalizado antes de qualquer valuation |
| 26/07/2026 | `financial_4d3b_integrity` | P4/P5 | Calibrações reconstruídas no schema atual e persistidas; MGLU3 estrutural, SUZB3/KLBN11 não promovidas; FCF permanece proxy por ausência de manutenção de capex explicitamente evidenciada | Obter divulgação auditável de manutenção de capex e drivers financeiros antes de liberar DCF |
| 26/07/2026 | `issuer_adjusted_fcf_20260726` | P5 | Evidência primária incorporada: FCF ajustado com maintenance capex explícito para SUZB3 e KLBN11; MGLU3 permanece bloqueada por ausência do split | Calibrar bridges e obter mercado PIT válido antes de DCF |
| 26/07/2026 | `32e4355ac11ec154db17f8c5c695403f66901639cb203df0087fe698642cac30` | P6 | Replay PIT canônico persistido em `audit.duckdb`: 34 cutoffs, 640 sessões, 10 avaliações, 34 snapshots, performance reconciliada; 0 entradas/saídas, NAV final igual ao capital inicial, custos/slippage 0 | Cobertura de decisões aprovadas, eventos corporativos PIT, impostos e kill-switch em staging |
| 26/07/2026 | `p6_allocation_eligibility` | P6 | Auditoria dos 5 decision snapshots: `0/5` elegíveis; MGLU3 alcançou `WATCH` upstream real, mas segue `WAIT_FOR_CONFIRMATION`/`ELEVATED_RISK`; nenhum approval foi criado | Resolver timing/risk com dados PIT suficientes antes de qualquer entrada simulada |
| 26/07/2026 | `p7_production_readiness` | P7 | Auditoria fail-closed executada: BUY/ordens/broker = 0; configuração e governança seguras; sandbox, reconciliação de broker, aprovação segregada e alertas externos ainda ausentes | Implementar somente em sandbox e manter produção bloqueada até aprovação formal |

## Backlog posterior

- [ ] Validação semântica adicional do MiroFish com dados upstream reais.
- [ ] Ablação histórica completa com e sem MiroFish.
- [ ] Replays diários 2024–2026, somente quando houver vintages PIT.
- [ ] Generalização para centenas de empresas após cobertura piloto.
- [ ] Eliminação de regras específicas por ticker.
- [ ] Hardening completo do sidecar e autenticação.
- [ ] Otimização de pesos após validação, nunca antes.
- [ ] Produção de `WATCH` não é objetivo obrigatório; `NO_ACTION` continua válido.
- [ ] Integração com corretora somente na Fase P7.
- [ ] `BUY` e execução real permanecem bloqueados.
