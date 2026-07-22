# Arquitetura

## 1. Pipeline

1. **Ingestão quantitativa**
   - macroeconomia, clima, energia, commodities, câmbio e curvas de juros;
   - cotações, liquidez, múltiplos, resultados e carteira atual.
2. **Ingestão documental/narrativa**
   - fatos relevantes, ITR/DFP, relatórios, comunicados e notícias;
   - vídeos e opiniões apenas como material secundário.
3. **Normalização de evidências**
   - cada afirmação recebe fonte, horário, entidade, horizonte e confiabilidade.
4. **Detecção de evento material**
   - novidade, divergência versus consenso, magnitude e persistência.
5. **Geração de cenários**
   - MiroFish explora comportamentos e efeitos de segunda ordem;
   - cenários são hipóteses, não sinais de compra.
6. **Grafo causal financeiro**
   - evento -> variável macro -> setor -> receita/custo/capital -> lucro/FCF -> valuation.
7. **Seleção de ativos**
   - fundamentos, exposição real, liquidez, valuation e sensibilidade ao cenário.
8. **Timing**
   - preço versus valor, tendência, volatilidade, catalisador e risco de já estar precificado.
9. **Tribunal**
   - macro, causal, fundamentalista, valuation, técnico, cético e risco.
10. **Política de decisão**
   - compra, observação, manutenção, redução ou nenhuma ação.
11. **Auditoria e aprendizado**
   - snapshot de tudo que era conhecido na data, acompanhamento e calibração.

## 2. Separação de responsabilidades

```text
macro_b3_bot
├── domain          contratos e regras puras
├── application     casos de uso e orquestração
├── adapters        BCB/CVM/FRED/MiroFish/b3_screener/legado
├── infrastructure  banco, cache, logs e agendamento
└── interfaces      CLI/API futura
```

## 3. Papel correto do MiroFish

Use MiroFish para:

- gerar cenários plausíveis e contrários;
- simular reações de agentes econômicos;
- descobrir efeitos de segunda/terceira ordem;
- produzir perguntas e variáveis que o pipeline quantitativo deve verificar.

Não use MiroFish para:

- calcular valor justo;
- afirmar causalidade sem evidência;
- escolher ticker apenas por similaridade textual;
- substituir backtest, análise financeira ou controle de risco.

## 4. Trigger event-driven

A coleta pode rodar periodicamente, mas o pipeline completo só dispara quando pelo menos um gatilho é atendido:

- `novelty_score >= 0.65`;
- surpresa macro acima do limiar histórico;
- mudança relevante na probabilidade de cenário;
- novo documento corporativo material;
- alteração de preço/valuation suficiente para mudar a decisão;
- quebra de tese ou limite de risco.

O deduplicador calcula uma assinatura de evento e bloqueia relatórios repetidos.

## 5. Contratos de saída

Toda recomendação precisa conter:

- ativo e classe;
- ação recomendada;
- faixa de entrada, não preço pontual falso;
- horizonte;
- mecanismo causal em etapas;
- evidências com timestamp;
- cenário-base, otimista e adverso;
- probabilidade calibrada ou marcada como não calibrada;
- invalidadores objetivos;
- upside/downside estimado;
- tamanho máximo da posição;
- riscos de liquidez, correlação e concentração;
- nível de confiança e motivos de incerteza.
