# Política de decisão

## Score total

```text
score =
  0.18 * evidence_quality
+ 0.17 * scenario_probability
+ 0.18 * causal_strength
+ 0.15 * company_exposure
+ 0.10 * fundamental_quality
+ 0.08 * valuation_attractiveness
+ 0.07 * entry_timing
+ 0.07 * portfolio_fit
- penalties
```

Todos os componentes estão em `[0, 1]`.

## Portões obrigatórios

Uma recomendação `BUY` só pode ser emitida quando:

- `score >= 0.72`;
- `confidence >= 0.65`;
- pelo menos 3 evidências independentes, sendo 1 fonte primária;
- `reward_risk >= 1.8`;
- liquidez e tamanho de posição aprovados;
- ausência de veto do agente cético ou de risco;
- evento ainda não totalmente refletido no preço;
- não houver dado crítico vencido.

Caso contrário: `WATCH`, `HOLD`, `REDUCE` ou `NO_ACTION`.

## Penalidades sugeridas

- fonte única: `-0.15`;
- narrativa de YouTube não confirmada: `-0.20`;
- evento com mais de 30 dias sem atualização: `-0.10`;
- ativo ilíquido: `-0.20`;
- tese já precificada: `-0.15`;
- correlação excessiva com carteira: `-0.10`;
- cenário não calibrado: `-0.05`;

## Faixa de entrada

A faixa deve resultar de:

- valor justo por cenários;
- margem de segurança mínima;
- volatilidade/ATR;
- suporte de liquidez;
- calendário do catalisador.

Nunca inventar “melhor momento” com precisão diária quando o sinal é macro de 6–18 meses.
