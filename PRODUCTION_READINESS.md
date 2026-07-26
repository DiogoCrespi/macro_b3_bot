
# Avaliação de prontidão para produção

**Data da avaliação:** 25/07/2026
**Estado avaliado:** commit `2acfdf2`
**Última suíte local:** 350 testes aprovados em 79,20 s em Python 3.12

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
- O CI publicado foi aprovado em Python 3.11 e 3.12; a suíte local atual tem 345 testes.

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
| Staging/piloto interno | Sim        | Dados PIT, logs, backup e `BUY` desabilitado         |
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

- [x] Suíte local completa executada (`342 passed` em Python 3.12).
- [x] Ruff e `git diff --check` aprovados.
- [x] Bytecodes rastreados removidos do Git.
- [x] Fixar dependências Python 3.12 em `requirements-py312.lock` e versão em `.python-version`.
- [x] Criar `data/audits/baseline_reproducible.json` com commit, configuração e checksums.
- [x] Separar dados controlados e upstream em `data/README.md` e no manifesto.

**Saída:** `baseline_reproducible.json`, `requirements-py312.lock`, `.python-version` e
`data/README.md`.

### Fase P1 — Staging operacional seguro

**Status:** `EM ANDAMENTO`
**Objetivo:** executar o orquestrador e o sidecar como serviço controlado, sem ordens.

- [x] Containerizar o orquestrador e declarar o sidecar no Compose.
- [x] Criar configuração de staging distinta de `.env` de desenvolvimento.
- [x] Fixar a imagem do sidecar por digest imutável no ambiente de staging.
- [x] Declarar secrets externos via Compose; rotação da chave Zep ainda pendente.
- [x] Implementar health checks, timeout, retry básico e circuit breaker.
- [x] Implementar cancelamento cooperativo do workflow após falha/timeout, via `/api/simulation/stop`.
- [x] Implementar wrapper de scheduler com lock, idempotência e status por execução.
- [ ] Integrar o wrapper a um scheduler externo com comando e `run_id` point-in-time.
- [x] Definir volume persistente e backup verificado do DuckDB.
- [x] Testar cópia restaurável em ambiente temporário.
- [x] Definir retenção e limpeza automática de backups (`--keep`, padrão 7).

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

- [ ] Autenticação e autorização para revisão, decisão e administração.
- [ ] RBAC separando operador, revisor e administrador.
- [x] Núcleo de log append-only com hash chain para revisão/binding/decisão.
- [x] Métricas de ingestão, atraso PIT, falhas do sidecar, hipóteses e bloqueios em snapshots JSON.
- [x] Avaliação determinística de alertas para atraso PIT, conflitos, falhas e baixa aprovação.
- [ ] Dashboard de teses, hipóteses, `WATCH`, `NO_ACTION` e invalidadores.
- [x] Kill switch fail-closed com permissão administrativa e motivo obrigatório.

**Evidência P2 inicial:** `application/governance.py`, `application/observability.py`,
`docs/STAGING_RUNBOOK.md` e testes validam RBAC mínimo, ledger append-only, kill switch,
snapshots de métricas e alertas determinísticos. Autenticação real, entrega de alertas,
dashboard visual e integração com monitoramento externo ainda estão pendentes.

### Fase P3 — Grounding e binding econômico real

**Status:** `PARCIALMENTE CONCLUÍDA`
**Objetivo:** obter hipóteses realmente compatíveis com eventos e estados PIT reais.

- [x] Remover templates locais de cenário.
- [x] Persistir relatório bruto, checksum e extração estruturada.
- [x] Rejeitar incompatibilidade semântica IPCA/global e ITR/tecnologia.
- [x] Separar revisão delegada de revisão humana.
- [x] Persistir revisão, validação e binding append-only.
- [x] Proibir matching por número ou substring isolada.
- [ ] Executar um relatório MiroFish semanticamente compatível com evento brasileiro real.
- [ ] Obter `source_document_ids` preservados na hipótese.
- [ ] Obter `SUPPORTED + BOUND + CONSISTENT` sem conflito.
- [ ] Demonstrar candidato setorial ativo no mesmo corte.

**Aceite:** pelo menos um caso real passa todos os gates sem alterar o payload canônico.
O caso atual continua rejeitado corretamente e não deve ser promovido artificialmente.

### Fase P4 — Validação histórica dos bridges

**Status:** `PENDENTE`
**Objetivo:** medir se os impactos financeiros têm poder explicativo fora da amostra.

- [ ] Replays walk-forward por data de disponibilidade.
- [ ] Mínimo de cinco janelas por bridge; preferir oito a doze trimestres.
- [ ] MGLU3: juros líquidos, caixa sensível, repricing e derivativos.
- [ ] SUZB3: FX, celulose, volume, custos cambiais e margem incremental.
- [ ] KLBN11: FX, CDI/SOFR e IPCA em bridges separados.
- [ ] MAE/RMSE fora da amostra e estabilidade de sinal.
- [ ] Precision@k, hit rate, drawdown, turnover e custos.
- [ ] Ablação `DETERMINISTIC_ONLY` versus `DETERMINISTIC_PLUS_MIROFISH`.

**Aceite:** resultados persistidos com premissas, erro observado e intervalos; nenhum
coeficiente in-sample é promovido automaticamente a calibração validada.

### Fase P5 — Valuation readiness

**Status:** `BLOQUEADA POR P4`
**Objetivo:** impedir valuation artificial e liberar somente dados qualificados.

- [ ] Gate formal `VALUATION_READY`/`VALUATION_BLOCKED`.
- [ ] Bloqueio explícito para FCF estatístico não normalizado.
- [ ] Baselines e mercado point-in-time reproduzíveis.
- [ ] Múltiplos observados com amostra e data válidas.
- [ ] Cenários de receita, EBITDA, lucro e FCF com premissas auditáveis.
- [ ] DCF somente para empresas que passarem todos os gates.

**Aceite:** cada bloqueio informa código e evidência; nenhum preço-alvo é produzido para
empresa sem FCF e calibração aptos.

### Fase P6 — Paper portfolio e decisão controlada

**Status:** `BLOQUEADA POR P5`
**Objetivo:** testar decisões sem capital real.

- [ ] Paper portfolio com custos, impostos, slippage e liquidez.
- [ ] Reconciliação diária e tratamento de eventos corporativos.
- [ ] Limites de concentração, exposição e perda.
- [ ] Aprovação humana autenticada antes de qualquer ação externa.
- [ ] Relatório de performance e calibração por período.

**Aceite:** período mínimo de paper trading definido, reconciliação sem diferenças
materiais e kill switch testado.

### Fase P7 — Produção decisória e execução

**Status:** `BLOQUEADA`
**Objetivo:** somente após P0–P6, avaliar produção e eventual integração de corretora.

- [ ] Revisão de segurança e compliance.
- [ ] Segregação de credenciais de corretora.
- [ ] Sandbox de ordens e reconciliação com broker.
- [ ] Aprovação humana segregada e auditável.
- [ ] Limites pré-trade, pós-trade e kill switch independente.
- [ ] Rollback operacional e plano de incidente.

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

Isso fecha os defeitos de integridade do 5B. A homologação econômica ainda depende de
eventos reais com candidato setorial ativo, claims/documentos PIT compatíveis e replays
históricos suficientes.

---

## Registro de progresso

| Data | Commit | Fase | Resultado | Próximo bloqueio |
|---|---|---|---|---|
| 25/07/2026 | `6e3fefa` | P3 | 5B.1 implementado; hipótese atual rejeitada corretamente | Caso real `SUPPORTED + BOUND` |
| 25/07/2026 | `a3dc7e1` | P0 | 341 testes, Ruff, Python 3.12 e manifesto reproduzível aprovados | P1: staging operacional seguro |
| 25/07/2026 | `f4eb3aa` | P1 | Digest GHCR multi-arquitetura fixado, validador fail-closed, worker local/Docker, adaptadores Task Scheduler validados em WhatIf e invocador executado com run_id, cancelamento cooperativo, Docker build, Compose config, health check, backup/restore, lock/idempotência e circuit breaker aprovados; 345 testes, Ruff e diff check | Registro persistente do scheduler, rotação de secrets e smoke pós-pull |
| 25/07/2026 | `2acfdf2` | P2 | RBAC mínimo, ledger append-only com hash chain, kill switch, métricas, alertas e runbook inicial implementados e testados; 350 testes | Autenticação real, entrega de alertas, dashboard e monitoramento externo |

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
