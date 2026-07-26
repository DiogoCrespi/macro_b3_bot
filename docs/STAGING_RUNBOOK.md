# Runbook de staging

## Falha do sidecar

1. Consultar o snapshot de métricas e o `run_id` no diretório de dados.
2. Se houver `SIDECAR_FAILURE_BURST`, verificar saúde, circuito e logs do MiroFish.
3. Reexecutar somente com novo `STAGING_RUN_ID`; o mesmo ID é idempotente.
4. Em timeout, o worker solicita `/api/simulation/stop` e registra o resultado.

## Conflito ou dado atrasado

`UNRESOLVED_CONFLICT` e `PIT_LATE_DATA` exigem investigação antes de qualquer uso
decisório. A saída deve permanecer `NO_ACTION` até resolução documentada.

## Kill switch e rollback

Um administrador autenticado deve ativar o kill switch com motivo, sem apagar o ledger.
Para rollback: parar o job, restaurar o último backup DuckDB, fixar a imagem no digest
anterior e registrar a ação no ledger append-only.
