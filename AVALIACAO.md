# Avaliação (fase 5)

## Recuperação (parte 1)

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

## Resposta final com repetições (fase 5, parte 2)

`python -m src.avaliar_respostas --scenario <id> --repeticoes 3 [--retomar]` roda cada pergunta N vezes com o modelo (Groq, `openai/gpt-oss-120b`) e mede o que muda entre as repetições. A rodada anda uma repetição de cada vez sobre todas as perguntas e grava em `logs/` (fora do Git) o texto de cada resposta. O modelo varia de uma repetição para outra, e é isso que se quer ver.

**O que foi medido: só a saúde, 13 perguntas × 3 repetições (185 mil tokens).** O RJ parou no RJ-04 da primeira repetição e a pesquisa não começou, porque o plano gratuito da Groq esgotou o limite diário. Para continuar sem refazer o que já deu certo: `--retomar`. Depois dessa rodada entrou um plano de reserva sem o modelo para o RJ-04 (ver PROGRESSO.md); ele ainda não foi medido.

| Medida (saúde) | Resultado |
|---|---|
| Rota escolhida bate com o tipo de evidência esperado (conferência grossa) | 38 de 39 execuções |
| Recusa igual nas 3 repetições | 12 de 13 perguntas (a exceção é a pergunta da lacuna, abaixo) |
| Rota igual nas 3 repetições | 12 de 13 perguntas |
| Perguntas de recusa (dose individual, plano de saúde, dispensação, telefone) | recusadas nas 3 repetições; a de dose pela regra de entrada, sem chamar o modelo |
| Número calculado em SQL (apresentações do componente Especializado) | 314 nas 3 repetições, confere |
| Texto da resposta | difere em toda pergunta respondida (3 textos distintos nas 3 repetições), o que é esperado de um modelo de linguagem; só as recusas por regra repetem o texto idêntico |

O que as repetições mostraram:

- **SA-02 (o que mudou na artrite reumatoide) ficou estável depois da correção da busca por versão.** Nas 3 repetições o modelo comparou a PC 33/2026 com a PC 16/2021, com as datas. Isso só vale para a escolha das versões: não conferi se cada diferença citada ("a versão 2026 acrescenta o Art. 2º...") está de fato ausente da versão de 2021 nos trechos recuperados. Dizer "acrescenta" a partir do que um trecho tem e o outro não traz é uma inferência que a evidência pode não sustentar.
  - **Conferi, e a inferência não se sustentou.** Procurei no texto inteiro das duas versões (51 trechos da de 2026 e 214 da de 2021, sem usar o modelo) cada coisa que as 3 respostas deram como nova em 2026. Seis afirmações: **5 estão também na versão de 2021** (o Termo de Esclarecimento e Responsabilidade; a cientificação do paciente, no Art. 2º da portaria; a estruturação da rede pelos gestores, no Art. 3º; as evidências em GRADEpro e os eventos adversos como hepatotoxicidade; e a consulta à RENAME e à disponibilidade na Assistência Farmacêutica) e **1 é de fato nova** (as deliberações da Conitec de 2025, que a versão de 2021 não poderia citar). Uma sétima (a atualização da busca da literatura) não consegui classificar. Ou seja, as respostas diziam "a versão 2021 não contém" porque o trecho que veio não continha, e o texto completo contém. As datas e as versões comparadas estavam certas; o conteúdo das diferenças, não. A regra de resposta foi endurecida (descrever o que cada versão traz nos trechos recebidos, sem "acrescenta" nem "não consta"), mas ainda não foi repetida com o modelo, e uma comparação confiável entre versões exigiria comparar os textos completos por seção, o que o sistema não faz.
- **SA-10 (medicamentos do PCDT de Parkinson fora da RENAME) falhou nas 3 repetições, como previsto.** A consulta à RENAME não leva filtro e devolve itens quaisquer. Em duas repetições o modelo disse que não dava para responder (honesto); na outra afirmou que os itens listados "não constam no PCDT", o que é enganoso. É a falha mais perigosa do cenário porque nem sempre o modelo desconfia.
  - **Correção feita depois da rodada, ainda não medida com o modelo.** A seção "Fármacos" dos protocolos agora vira a tabela `pcdt_medicamento` (69 linhas: Parkinson 2017, com 11 medicamentos, e artrite reumatoide de 2017, 2019 e 2021), com a marca de constar ou não na RENAME, calculada em código pelo princípio ativo (sal e forma não entram na comparação). Nas 69 linhas, todos os princípios ativos constam na RENAME 2024; para o Parkinson a resposta correta passa a ser "nenhum dos 11 deixa de constar", com a ressalva de que só a seção Fármacos foi lida. A consulta foi testada com um plano montado à mão, sem o modelo; falta repetir a pergunta com o modelo quando o limite da Groq voltar. **Cobertura:** a artrite de 2026 (a vigente) e o diabetes tipo 1 não têm a lista lida, porque o texto não segue o formato "nome: apresentação"; nelas a comparação continua sem suporte, e o sistema deve dizer isso.
- **A pergunta da lacuna (versão em janeiro de 2021) é instável.** Em 2 das 3 repetições o sistema recusou por falta de evidência; na outra, respondeu que a consulta não achou registro e que isso não prova que não haja versão. Nenhuma inventou uma versão, mas o comportamento não é consistente, e nenhuma explicou que o corpus não tem versão naquele período.

**Limites.** A conferência de rota é grossa (tipo de evidência, não o plano exato) e não há juiz automático de qualidade: o texto das respostas precisa de leitura. Só uma pergunta da saúde tem número conferível por SQL. As 13 perguntas são poucas para estimar taxas.

## O que não foi medido

- **Rerank por cross-encoder.** Não está implementado. Exige baixar um modelo de reranking (da ordem de gigabytes), o que não foi autorizado. A comparação "RRF + rerank" da fase 5 fica em aberto.
- **Resposta final no RJ e na pesquisa.** Só a saúde tem as 3 repetições (seção acima); o RJ tem 3 perguntas com 1 repetição e a pesquisa nenhuma, por causa do limite diário da Groq.
- **Perguntas de dados** (estruturada e agregada) não passam pela recuperação de trechos e ficam fora.

## Limites desta avaliação

- **São 34 perguntas.** Uma pergunta muda uma média em 0,03. Diferenças pequenas não devem ser lidas como ranking.
- **Eu escrevi as perguntas e os critérios.** Um critério por expressão regular pode marcar como relevante um trecho que não responde bem, ou deixar de fora um que responde com outras palavras. A precisão@5 é a métrica mais sensível a isso.
- **O embedding é pequeno** (0,6 bilhão de parâmetros, em CPU). Com outro modelo de embedding o denso pode subir.
- **Os índices do RJ e da pesquisa foram reconstruídos** com a ingestão atual (leitura de PDF em duas colunas e cabeçalhos de página). Na pesquisa os trechos saíram idênticos (mesmo SHA-256); no RJ a base de editais foi de 1.458 para 1.456 trechos (versão v2), e as métricas de recuperação não mudaram (MRR@10: BM25 0,687, denso 0,745, RRF 0,722). A v3 dos editais do RJ não foi reindexada.

## Decisões

1. **Manter o RRF como busca padrão.** Tem o melhor MRR entre os sistemas de k=60 e um hit@5 (0,94) próximo ao do denso. Não é imune a falha: quando o BM25 não traz nada relevante, ele pode atrapalhar (as duas perguntas acima).
2. **Não trocar k_rrf por 10 ainda.** O sinal é fraco e foi escolhido nos mesmos dados. Reavaliar com um conjunto maior, escrito antes de olhar os resultados.
3. **Manter o filtro de vigência e de doença**, que já é necessário por correção (não misturar versões), e que nos dados também não piorou nada.
4. **Próximos passos para fechar a fase 5:** continuar a rodada de respostas do RJ e da pesquisa com `--retomar` quando o limite da Groq voltar; autorizar o download de um modelo de rerank e medir "RRF + rerank"; ampliar o conjunto de perguntas.
5. **Falha conhecida:** o SA-10. A causa (comparar sem ter extraído os medicamentos do protocolo) foi tratada com a tabela `pcdt_medicamento`, mas a pergunta só será dada como corrigida depois de repetida com o modelo, e a artrite de 2026 e o diabetes continuam sem a lista.
