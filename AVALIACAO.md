# Avaliação da recuperação (fase 5, parte 1)

Mede só a **recuperação** de trechos (BM25, busca densa e fusão RRF), sem modelo de linguagem. Roda com
`python -m src.avaliar --scenario <rj|saude|pesquisa>`; o resultado completo, por pergunta, vai para `logs/` (fora do Git).

## Método

- **Perguntas.** 34 em `scenarios/<id>/eval/retrieval_gold.json` (RJ 12, saúde 12, pesquisa 10), escritas à mão em linguagem natural, algumas com vocabulário diferente do texto.
- **O que é um trecho relevante.** Um critério, e não uma lista de ids: expressões regulares sobre o texto e os metadados do trecho (sem acento e sem caixa). O conjunto relevante é recalculado sobre o índice real, então o critério não depende de numeração de trechos. Cada pergunta precisa ter ao menos um trecho relevante; todas têm.
- **Sistemas.** `bm25`; `denso` (FAISS, embedding `qwen3-embedding:0.6b`); `rrf` (BM25 + denso, k_rrf = 60, o padrão do motor); `rrf_k10` (k_rrf = 10); `rrf_sem_filtro` (o RRF sem os filtros de doença e de vigência, só com o de sensibilidade).
- **Métricas** no topo 5/10: hit@1, hit@5 (há ao menos um relevante?), MRR@10 e precisão@5.
- **Repetições.** Cada sistema roda 3 vezes por pergunta. As 3 repetições deram a mesma ordem em todos os casos (a recuperação é determinística), então elas servem para confirmar isso, e não para medir variação.

## Resultado (34 perguntas)

| Sistema | hit@1 | hit@5 | MRR@10 | P@5 | Tempo mediano |
|---|---|---|---|---|---|
| BM25 | 0,676 | 0,853 | 0,751 | 0,435 | 5 a 18 ms |
| Denso | 0,588 | **0,971** | 0,736 | 0,406 | ~400 ms (inclui o embedding da pergunta) |
| RRF (k=60, padrão) | 0,676 | 0,941 | 0,799 | 0,471 | ~420 ms |
| RRF (k=10) | 0,735 | 0,941 | **0,833** | 0,476 | ~420 ms |
| RRF sem filtro de vigência | 0,647 | 0,912 | 0,770 | 0,435 | ~420 ms |

Por cenário (MRR@10, BM25 / denso / RRF): RJ 0,687 / 0,745 / 0,722; saúde 0,711 / 0,783 / 0,792; pesquisa 0,875 / 0,667 / 0,900.

## O que os números sustentam

Diferenças pareadas no MRR@10, com intervalo de confiança de 95% por bootstrap (5.000 reamostragens):

| Comparação | Diferença | IC 95% | Vitórias / derrotas / empates |
|---|---|---|---|
| RRF - BM25 | +0,048 | [-0,040; +0,137] | 9 / 3 / 22 |
| RRF - denso | +0,063 | [-0,051; +0,180] | 9 / 5 / 20 |
| RRF k=10 - RRF k=60 | +0,034 | [0,000; +0,088] | 2 / 0 / 32 |
| RRF - RRF sem filtro | +0,029 | [0,000; +0,078] | 2 / 0 / 32 |

- **O RRF tem a maior média de MRR entre os sistemas de k=60, mas o intervalo contra BM25 e contra denso inclui zero.** Com 34 perguntas, isso não prova que o híbrido é melhor; é o que os dados favorecem.
- **Os sistemas são complementares.** O denso acha algo relevante no top 5 em 97% das perguntas, mas põe o relevante em primeiro lugar menos vezes (59%). O BM25 acerta mais o primeiro lugar (68%) mas falha em 15% das perguntas. O RRF fica no meio e ganha no ranking.
- **O RRF perdeu duas perguntas que o denso acertou** (SA-R02, insulina no diabetes tipo 1, e SA-R03, medicamentos do Parkinson). Nelas o BM25 não trouxe nada relevante e puxou o resultado para baixo. É o custo de fundir uma lista ruim com uma boa.
- **O filtro de doença e de vigência ajuda, pouco e sem folga estatística.** Nas 9 perguntas de saúde que o usam, o MRR vai de 0,611 (sem filtro) para 0,722 (com filtro). Em 32 das 34 perguntas o resultado é idêntico.
- **k_rrf = 10 parece melhor que 60** (2 vitórias, 0 derrotas), mas escolhi esse valor olhando as mesmas perguntas em que ele é medido. Não alterei o padrão do motor por isso.

## O que não foi medido

- **Rerank por cross-encoder.** Não está implementado. Exige baixar um modelo de reranking (da ordem de gigabytes), o que não foi autorizado. A comparação "RRF + rerank" da fase 5 fica em aberto.
- **Resposta final.** Esta avaliação mede se os trechos certos chegam ao topo, não se o modelo responde certo. A medição ponta a ponta com 3 repetições por pergunta (o modelo varia entre repetições) ainda não foi feita; as rodadas reais anteriores estão em `PROGRESSO.md`, uma execução por pergunta.
- **Perguntas de dados** (estruturada e agregada) não passam pela recuperação de trechos e ficam fora.

## Limites desta avaliação

- **São 34 perguntas.** Uma pergunta muda uma média em 0,03. Diferenças pequenas não devem ser lidas como ranking.
- **Eu escrevi as perguntas e os critérios.** Um critério por expressão regular pode marcar como relevante um trecho que não responde bem, ou deixar de fora um que responde com outras palavras. A precisão@5 é a métrica mais sensível a isso.
- **O embedding é pequeno** (0,6 bilhão de parâmetros, em CPU). Com outro modelo de embedding o denso pode subir.
- **Os índices do RJ e da pesquisa foram construídos antes das mudanças de leitura de PDF em duas colunas e de cabeçalhos de página** na ingestão; reconstruí-los pode mudar os trechos.

## Decisões

1. **Manter o RRF como busca padrão.** Tem o melhor MRR entre os sistemas de k=60 e um hit@5 (0,94) próximo ao do denso. Não é imune a falha: quando o BM25 não traz nada relevante, ele pode atrapalhar (as duas perguntas acima).
2. **Não trocar k_rrf por 10 ainda.** O sinal é fraco e foi escolhido nos mesmos dados. Reavaliar com um conjunto maior, escrito antes de olhar os resultados.
3. **Manter o filtro de vigência e de doença**, que já é necessário por correção (não misturar versões), e que nos dados também não piorou nada.
4. **Próximos passos para fechar a fase 5:** autorizar o download de um modelo de rerank e medir "RRF + rerank"; ampliar o conjunto de perguntas; medir a resposta final com 3 repetições.
