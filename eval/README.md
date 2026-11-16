# eval

Avaliação consolidada. Os resultados e a metodologia estão em [../AVALIACAO.md](../AVALIACAO.md); os conjuntos de perguntas ficam em `scenarios/<nome>/eval/`.

```bash
python -m src.avaliar --scenario saude                          # recuperação: BM25, vetores, RRF (sem modelo de linguagem)
python -m src.avaliar_respostas --scenario saude --repeticoes 3 # resposta final com o modelo (usa a cota da Groq)
python -m src.avaliar_respostas --scenario rj --retomar         # continua uma rodada interrompida pelo limite
```

O rerank por cross-encoder não foi implementado.
