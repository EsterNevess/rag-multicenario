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
| RRF sem filtro de vigência | 0,618 | 0,941 | 0,760 | 0,435 | ~420 ms |

Por cenário (MRR@10, BM25 / denso / RRF): RJ 0,687 / 0,745 / 0,722; saúde 0,711 / 0,783 / 0,792; pesquisa 0,875 / 0,667 / 0,900.

## O que os números sustentam

Diferenças pareadas no MRR@10, com intervalo de confiança de 95% por bootstrap (5.000 reamostragens):

| Comparação | Diferença | IC 95% | Vitórias / derrotas / empates |
|---|---|---|---|
| RRF - BM25 | +0,048 | [-0,040; +0,137] | 9 / 3 / 22 |
| RRF - denso | +0,063 | [-0,051; +0,180] | 9 / 5 / 20 |
| RRF k=10 - RRF k=60 | +0,034 | [0,000; +0,088] | 2 / 0 / 32 |
| RRF - RRF sem filtro | +0,039 | [-0,007; +0,103] | 3 / 1 / 30 |

- **O RRF tem a maior média de MRR entre os sistemas de k=60, mas o intervalo contra BM25 e contra denso inclui zero.** Com 34 perguntas, isso não prova que o híbrido é melhor; é o que os dados favorecem.
- **Os sistemas são complementares.** O denso acha algo relevante no top 5 em 97% das perguntas, mas põe o relevante em primeiro lugar menos vezes (59%). O BM25 acerta mais o primeiro lugar (68%) mas falha em 15% das perguntas. O RRF fica no meio e ganha no ranking.
- **O RRF perdeu duas perguntas que o denso acertou** (SA-R02, insulina no diabetes tipo 1, e SA-R03, medicamentos do Parkinson). Nelas o BM25 não trouxe nada relevante e puxou o resultado para baixo. É o custo de fundir uma lista ruim com uma boa.
- **O filtro de doença e de vigência ajuda, pouco e sem folga estatística nas 34 originais** (+0,039, 3 vitórias, 1 derrota, 30 empates). As linhas "sem filtro" desta seção e da seguinte foram recalculadas depois do corpus v2 (artrite de 2020 e diabetes tipo 2 entraram; as perguntas de diabetes tipo 1 passaram a filtrar por "tipo 1"); com o corpus v1 o efeito era +0,029, sem derrotas.
- **k_rrf = 10 parece melhor que 60** (2 vitórias, 0 derrotas), mas escolhi esse valor olhando as mesmas perguntas em que ele é medido. Não alterei o padrão do motor por isso.

## Segundo conjunto: 25 perguntas novas

Para testar o que foi decidido com as 34 primeiras, escrevi 25 perguntas novas (RJ 9, saúde 8, pesquisa 8) em `scenarios/<id>/eval/retrieval_gold_novas.json`, depois de tomar as decisões abaixo e sem olhar o ranking dos sistemas nelas. Os critérios de relevância só poderiam ser ajustados se uma pergunta ficasse sem nenhum trecho relevante no índice; isso não aconteceu: as 25 foram válidas de primeira. Roda com `python -m src.avaliar --scenario <id> --conjunto retrieval_gold_novas`.

| Sistema (25 novas) | hit@1 | hit@5 | MRR@10 | P@5 |
|---|---|---|---|---|
| BM25 | 0,560 | 0,800 | 0,666 | 0,552 |
| Denso | 0,560 | **0,880** | 0,683 | 0,504 |
| RRF (k=60, padrão) | **0,640** | **0,880** | 0,731 | **0,560** |
| RRF (k=10) | **0,640** | 0,840 | **0,737** | 0,544 |
| RRF sem filtro de vigência | 0,520 | 0,840 | 0,634 | 0,464 |

| Comparação (MRR@10) | Novas (25) | Todas (59) |
|---|---|---|
| RRF - BM25 | +0,065 [-0,038; +0,178] | +0,055 [-0,011; +0,125] |
| RRF - denso | +0,049 [-0,078; +0,179] | +0,057 [-0,026; +0,145] |
| RRF k=10 - RRF k=60 | +0,005 [-0,004; +0,020] | +0,022 [-0,001; +0,053] |
| RRF - RRF sem filtro | +0,098 [+0,002; +0,203] | +0,064 [+0,014; +0,124] |

O que o conjunto novo mostra:

- **A vantagem do k_rrf = 10 não se repetiu.** Nas perguntas novas a diferença é +0,005 (1 vitória, 1 derrota, 23 empates). O +0,034 do primeiro conjunto era em grande parte efeito de ter escolhido o valor olhando aquelas perguntas. Manter k = 60.
- **O RRF continua à frente de BM25 e denso, no mesmo tamanho (+0,05 a +0,07), mas o intervalo ainda inclui zero** nos dois conjuntos e nos 59 juntos (o de RRF contra BM25 por pouco: [-0,011; +0,125]). A direção é consistente; a prova estatística, não.
- **O filtro de doença e de vigência é a diferença mais firme.** Nas 25 novas (+0,098, intervalo sem zero por pouco) e nos 59 juntos (+0,064 [+0,014; +0,124], 7 vitórias, 2 derrotas, 50 empates). Ajuda em perguntas de saúde; nas demais não muda nada. Na saúde, sem o filtro o MRR das novas cai de 0,806 para 0,501.
- **Os conjuntos não são independentes do meu olhar.** Eu escrevi todas as perguntas e todos os critérios, e já conhecia o corpus e os pontos fracos dos sistemas. O conjunto novo é mais honesto para decidir o k do que o primeiro, mas continua sendo meu.

## Rerank por cross-encoder

O rerank lê a pergunta e cada trecho juntos e reordena os 20 primeiros da fusão RRF. O modelo é o `BAAI/bge-reranker-v2-m3` (multilíngue, Apache-2.0), na conversão ONNX quantizada em 8 bits (`onnx-community/bge-reranker-v2-m3-ONNX`, 544 MB), executado com `onnxruntime` em CPU, sem PyTorch. Mediu-se nas mesmas 59 perguntas (34 + 25), sem tocar nos critérios: `python -m src.avaliar --scenario <id>` passa a incluir o sistema `rrf_rerank` quando os arquivos do modelo existem em `data/models/`.

| Sistema (59 perguntas) | hit@1 | hit@5 | MRR@10 | P@5 |
|---|---|---|---|---|
| BM25 | 0,627 | 0,831 | 0,715 | 0,485 |
| Denso | 0,576 | 0,932 | 0,713 | 0,447 |
| RRF (k=60, padrão) | 0,661 | 0,915 | 0,770 | 0,512 |
| RRF (k=10) | 0,695 | 0,898 | 0,792 | 0,505 |
| **RRF + rerank** | **0,814** | **0,966** | **0,871** | **0,576** |

| Comparação (MRR@10) | Diferença | IC 95% | Vitórias / derrotas / empates |
|---|---|---|---|
| RRF + rerank - RRF, 59 perguntas | +0,101 | [+0,025; +0,180] | 14 / 4 / 41 |
| RRF + rerank - RRF, só o conjunto original (34) | +0,056 | [-0,022; +0,140] | 7 / 3 / 24 |
| RRF + rerank - RRF, só o conjunto novo (25) | +0,162 | [+0,029; +0,308] | 7 / 1 / 17 |
| RRF + rerank - BM25 (59) | +0,157 | [+0,072; +0,248] | 19 / 3 / 37 |
| RRF + rerank - denso (59) | +0,158 | [+0,073; +0,248] | 19 / 6 / 34 |

Por cenário (MRR@10, RRF -> RRF + rerank): RJ 0,722 -> 0,857 (+0,135, [-0,008; +0,286]); saúde 0,797 -> 0,858 (+0,061, [-0,068; +0,198]); pesquisa 0,796 -> 0,903 (+0,106, [0,000; +0,227], e o BM25 sozinho já tinha 0,847).

O que isso sustenta:

- **É a primeira diferença de ordenação com intervalo sem zero.** O rerank melhora o MRR em +0,10 (vantagem concentrada no primeiro lugar: hit@1 de 0,661 para 0,814) e aparece também no conjunto novo, escrito depois das decisões, onde o ganho é maior (+0,162). Em cada cenário isolado o intervalo ainda inclui zero (ou encosta nele), porque há poucas perguntas.
- **Não é uma melhoria em tudo.** Em 4 das 59 perguntas o rerank piorou o MRR (RJ-N04, SA-R04, SA-R05 e PE-R05; a maior queda é −0,67), em 14 melhorou e em 41 deu o mesmo. O hit@5 sobe pouco (0,915 -> 0,966): ele reorganiza a ordem mais do que acha trechos que a fusão perdeu, porque só vê os 20 primeiros.
- **O custo é alto em CPU.** Mediana de ~19 s por busca (o RRF leva ~0,3 s), com 20 candidatos de até 512 tokens por pergunta. Por isso fica **desligado por padrão** (`RERANK=true` no `.env` liga). Reduzir o número de candidatos ou o tamanho do trecho deve baratear, mas não foi medido.
- **Medi a ordenação dos trechos, não a resposta final.** Não sei se melhor ordenação melhora a resposta escrita pelo modelo, e isso depende da Groq. O modelo foi quantizado em 8 bits; não comparei com a versão sem quantização.
- **As perguntas e os critérios são meus**, como nos conjuntos anteriores (ver "Limites desta avaliação").

## Resposta final com repetições (fase 5, parte 2)

`python -m src.avaliar_respostas --scenario <id> --repeticoes 3 [--retomar]` roda cada pergunta N vezes com o modelo (Groq, `openai/gpt-oss-120b`) e mede o que muda entre as repetições. A rodada anda uma repetição de cada vez sobre todas as perguntas e grava em `logs/` (fora do Git) o texto de cada resposta. O modelo varia de uma repetição para outra, e é isso que se quer ver.

**O que foi medido: só a saúde, 13 perguntas × 3 repetições (185 mil tokens).** O RJ parou no RJ-04 da primeira repetição e a pesquisa não começou, porque o plano gratuito da Groq esgotou o limite diário. Para continuar sem refazer o que já deu certo: `--retomar`. Depois dessa rodada entrou um plano de reserva sem o modelo para o RJ-04 (ver PROGRESSO.md). Numa execução posterior com o modelo (16/11) o RJ-04 respondeu certo (25.005 de 35.423, 70,6%) sem precisar dele; o limite diário esgotou de novo em seguida.

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
  - **Conferi, e a inferência não se sustentou.** Procurei no texto inteiro das duas versões (51 trechos da de 2026 e 214 da de 2021, sem usar o modelo) cada coisa que as 3 respostas deram como nova em 2026. Seis afirmações: **5 estão também na versão de 2021** (o Termo de Esclarecimento e Responsabilidade; a cientificação do paciente, no Art. 2º da portaria; a estruturação da rede pelos gestores, no Art. 3º; as evidências em GRADEpro e os eventos adversos como hepatotoxicidade; e a consulta à RENAME e à disponibilidade na Assistência Farmacêutica) e **1 é de fato nova** (as deliberações da Conitec de 2025, que a versão de 2021 não poderia citar). Uma sétima (a atualização da busca da literatura) não consegui classificar. Ou seja, as respostas diziam "a versão 2021 não contém" porque o trecho que veio não continha, e o texto completo contém. As datas e as versões comparadas estavam certas; o conteúdo das diferenças, não. A regra de resposta foi endurecida (descrever o que cada versão traz nos trechos recebidos, sem "acrescenta" nem "não consta"), mas ainda não foi repetida com o modelo, e uma comparação confiável entre versões exigiria comparar os textos completos por seção.
  - **Comparação por seção (feita depois, ainda não medida com o modelo).** `src/pcdt_versoes.py` compara, em código e sobre o texto inteiro, cada par de versões consecutivas de uma doença e grava a tabela `pcdt_secao` (88 linhas, 4 pares da artrite reumatoide; Parkinson e diabetes têm uma versão só). Seções são casadas pelo título, sem a numeração; nas seções das duas versões conta-se quantas frases da nova não têm correspondente na anterior (e vice-versa). Entre a versão de 2021 e a de 2026: introdução, classificação estatística e critérios de inclusão e de exclusão ficaram 100% iguais (frase por frase); diagnóstico 96%; monitoramento 91%; regulação 78%; o Termo de Esclarecimento tem 2 frases e metade mudou; o **tratamento** foi a seção que mudou (135 frases novas, só 43,5% das frases da nova já estavam na anterior). As seções "Fármacos" e "Esquemas de administração" da versão de 2021 não existem como seção própria na de 2026, que traz os medicamentos dentro do item 6.2. Isso é coerente com o que a conferência do SA-02 achou (as supostas novidades da introdução e do termo já estavam na versão de 2021) e aponta onde a mudança real está.
  - **Conferência à mão do casamento de seções (16/11).** Li os 4 pares da artrite, linha por linha. **Nos pares em que a estrutura se manteve (2020 para 2021 e 2021 para 2026), o casamento estava certo.** Nos pares em que o protocolo foi reorganizado (2017 para 2019 e 2019 para 2020) havia erros de três tipos: (1) **8 linhas falsas**, fragmentos de tabela ("46 - A média do DM 0,19 menor") lidos como título de seção; (2) **1 par errado**, porque um título solto de 16 caracteres ("5. RECOMENDAÇÕES") pegava o par de "RECOMENDAÇÕES PARA PESQUISA" na frente do casamento exato; (3) cerca de **10 seções que só mudaram de nome** (monitorização e monitoramento, abordagem terapêutica e tratamento, critérios de elegibilidade e de inclusão, gestão e controle e regulação, fármacos e "fármacos e esquema de administração") apareciam como "só numa das versões". Corrigi com um filtro de fragmentos de tabela (só 4 dos 135 títulos distintos do corpus caíram nele, todos lixo), casamento em três passadas (exato, um contido no outro a partir de 8 caracteres, semelhança) e uma tabela de 4 sinônimos conferidos nas versões do corpus; a tabela foi de 105 para 88 linhas. **O que continua errado:** reorganizações de uma parte em várias (o "Tratamento" de 2017 virou "Abordagem terapêutica", "Fármacos e esquema" e "Fluxograma" em 2019) não casam 1 para 1, e a seção nova ou sumida é reportada como "só numa das versões" mesmo quando o conteúdo existia dentro de outra. Não medi uma taxa de erro depois da correção: reli os pares e os erros listados sumiram.
  - **Limites da comparação por seção.** Diz onde o texto mudou e quanto, não o que mudou clinicamente: uma frase "nova" pode ser a mesma ideia reescrita, e o PDF tem ruído (colunas, quebras). "Só na versão anterior" pode ser conteúdo de outro documento do mesmo PDF: o de 2021 inclui também a artrite idiopática juvenil, cujas seções (diagnóstico, casos especiais, monitorização) aparecem como "só na anterior". Títulos repetidos de anexos foram ignorados (61 na versão de 2021, contados no relatório). O casamento de seções por título pode errar quando o título muda muito.
- **SA-10 (medicamentos do PCDT de Parkinson fora da RENAME) falhou nas 3 repetições, como previsto.** A consulta à RENAME não leva filtro e devolve itens quaisquer. Em duas repetições o modelo disse que não dava para responder (honesto); na outra afirmou que os itens listados "não constam no PCDT", o que é enganoso. É a falha mais perigosa do cenário porque nem sempre o modelo desconfia.
  - **Correção feita depois da rodada, ainda não medida com o modelo.** A seção "Fármacos" dos protocolos agora vira a tabela `pcdt_medicamento` (69 linhas: Parkinson 2017, com 11 medicamentos, e artrite reumatoide de 2017, 2019 e 2021), com a marca de constar ou não na RENAME, calculada em código pelo princípio ativo (sal e forma não entram na comparação). Nas 69 linhas, todos os princípios ativos constam na RENAME 2024; para o Parkinson a resposta correta passa a ser "nenhum dos 11 deixa de constar", com a ressalva de que só a seção Fármacos foi lida. A consulta foi testada com um plano montado à mão, sem o modelo; falta repetir a pergunta com o modelo quando o limite da Groq voltar. **Cobertura (ampliada depois):** a primeira versão só lia o formato "nome: apresentação" (69 linhas). Agora há mais dois leitores, um para as listas por classe da artrite de 2026 (a vigente: 16 medicamentos, sem apresentação) e outro para o diabetes tipo 1 (4 insulinas, sem dois-pontos), e a tabela tem 89 linhas nas cinco versões. Todos os 89 constam na RENAME. Duas insulinas casaram só por correspondência aproximada ("insulina NPH" e "insulina humana NPH"; "insulina regular" e "insulina humana regular"), que o relatório da normalização registra. Cada leitor serve ao formato de um documento conhecido: se um protocolo mudar de formato, a versão aparece sem lista no relatório. Só entram os medicamentos que o protocolo lista; a lista da artrite de 2026 não traz anti-inflamatórios nem glicocorticoides, que as versões antigas traziam.
- **A pergunta da lacuna (versão em janeiro de 2021) é instável** (medida no corpus v1, que tinha um intervalo entre setembro de 2020 e setembro de 2021; no v2 a Portaria Conjunta 14/2020 cobre o período, e a pergunta passou a ter resposta). Em 2 das 3 repetições o sistema recusou por falta de evidência; na outra, respondeu que a consulta não achou registro e que isso não prova que não haja versão. Nenhuma inventou uma versão, mas o comportamento não é consistente, e nenhuma explicou que o corpus não tem versão naquele período.

**Limites.** A conferência de rota é grossa (tipo de evidência, não o plano exato) e não há juiz automático de qualidade: o texto das respostas precisa de leitura. Só uma pergunta da saúde tem número conferível por SQL. As 13 perguntas são poucas para estimar taxas.

## Redação de dados pessoais em texto novo

A ingestão de registros conversacionais troca por uma marca as frases que identificam pessoas ou trazem dado clínico individual (regras em `scenarios/saude/capacidades.yaml`) e mascara documentos, e-mails e telefones. Para medir em texto novo, escrevi três conjuntos de frases de ouvidoria, cada um com 30 frases sensíveis (nome, CPF, RG, cartão SUS, telefone, e-mail, endereço, nascimento, diagnóstico, dose, exame) e 12 reclamações comuns que não devem mudar. `python -m src.avaliar_redacao --scenario saude --conjunto <redacao_holdout | redacao_holdout_b | redacao_holdout_c>` aplica à mão o que a ingestão faz e confere se cada valor sensível sumiu.

| Conjunto | Quando foi escrito | Regras | Frases sensíveis protegidas | Comuns alteradas |
|---|---|---|---|---|
| A | depois das regras originais, sem ajustá-las | originais | **17 de 30 (57%)** | 0 de 12 |
| B | depois do A, antes de qualquer ajuste | originais | **11 de 30 (37%)** | 0 de 12 |
| A e B | os dois foram vistos ao ajustar as regras | ajustadas | 30 de 30 e 30 de 30 | 0 de 12 |
| **C** | **depois do ajuste, sem alterar regra por causa dele** | ajustadas | **13 de 30 (43%)**; 18 de 37 valores | **0 de 12** |

**O número que vale é o do conjunto C: 43% das frases sensíveis protegidas em texto novo.** O 30 de 30 em A e B não mede nada: as regras foram escritas olhando para as frases que vazavam (e eu vi a lista de vazamentos do B antes de ajustar), então são conjuntos de desenvolvimento. O ajuste acrescentou cerca de 15 padrões (nomes depois de "eu,", "seu", "Dr.", parentes sem vírgula; léxico de doenças com marcador de pessoa; verbos de dose; RG, data de nascimento e telefone fixo; "meu nome" em qualquer forma) e corrigiu o corte de frase depois de "Dr." e "Dra.", que separava o nome do título. **Em texto realmente novo, a cobertura ficou na mesma faixa de antes (57% e 37% sem ajuste, 43% com ele): regras e léxico escritos à mão não generalizam.**

O que escapou no conjunto C: doenças fora do léxico (psoríase, enxaqueca, osteoporose, covid, obesidade, labirintite, anemia, miopia, pneumonia), apelidos e nomes soltos ("a Rosinha", "a Neide", "o Zé Maria", "a Vera Lúcia Andrade"), identificadores que as regras não conhecem (prontuário, matrícula, CEP), endereço sem o verbo "moro" ("Sou da Rua Teodoro da Silva"), medicamento com dose por extenso ("dois comprimidos de captopril") e telefone com espaços ("21 98765 4321"). Não houve falso alarme em nenhum conjunto, nem nas frases comuns que citam doenças e hospitais com nome de pessoa.

**Conclusão: a redação é uma primeira camada e perde mais da metade das frases sensíveis em texto novo.** Ela não serve para dados reais; os registros daqui são sintéticos. Quem for ingerir dados reais precisa de outra abordagem (reconhecimento de nomes e de termos clínicos treinado para isso, ou nunca indexar o texto livre) e de revisão humana. Ampliar as regras com base no C o invalidaria: uma nova tentativa exige um conjunto D.

Limites: as 126 frases são minhas, num único cenário, e escritas por quem conhecia as regras.

## Conferência dos números da resposta

O código já conferia as citações (`[1]` precisa apontar para uma fonte que existe), mas um modelo pode citar a fonte certa e escrever um número que a fonte não traz. `src/numeros.py` extrai cada número do texto da resposta e o procura na evidência recebida e na pergunta, aceitando as formas de escrever o mesmo valor: milhar (25.005, 25 005, 25005), vírgula ou ponto decimal, percentual de uma fração (70,6% para 0,7059), arredondamento ao número de casas escrito, "milhão" e "mil", e as partes de uma data. Se algum não for encontrado, o modelo recebe uma segunda chance, com o número apontado; se o erro persistir, a resposta sai **com aviso** (`numeros_nao_conferidos` na resposta, "Atenção" na linha de comando e um aviso na tela) em vez de ser recusada, e quem lê decide. Os números se conferem no texto já mascarado, para o aviso nunca repetir um CPF ou telefone.

| O que foi medido | Resultado |
|---|---|
| Testes unitários e de integração (modelo simulado) | 35 testes, com 5 mutações do código conferidas |
| Respostas reais guardadas, evidência recoletada com o plano salvo (1 da Groq, 2 do modelo local) | 0 números não conferidos nas respostas certas |
| Mesmas respostas com o último dígito de cada número maior que 10 trocado | **12 de 12** corrupções apontadas |

**Limites.** (1) A amostra de respostas reais é de 3, porque só essas têm o plano e a evidência para recoletar; a taxa de falso alarme em volume só sai com a Groq. (2) Não confere o sentido: "25.005 é o total de quê?" passa. (3) Inteiros de 0 a 10 não são conferidos (são contagens e ordinais demais), então um "o total é 1" errado passa; foi o que aconteceu com o modelo local de 4 bilhões de parâmetros no RJ-04 ("o total de contratações é 1", com o total real de 35.423 na evidência). (4) Uma conta que o modelo faz (soma, diferença) não está na evidência e aparece como não conferida, o que é o desejado, mas também sinaliza contas certas. (5) Cada segunda tentativa custa uma chamada a mais ao modelo, só quando há número sem apoio.

## O que não foi medido

- **Rerank na resposta final.** O rerank foi medido na ordenação dos trechos (seção acima), mas não na resposta escrita pelo modelo, que espera o limite da Groq. Está implementado como opção desligada por padrão.
- **Resposta final no RJ e na pesquisa.** Só a saúde tem as 3 repetições (seção acima); o RJ tem 3 perguntas com 1 repetição e a pesquisa nenhuma, por causa do limite diário da Groq.
- **Perguntas de dados** (estruturada e agregada) não passam pela recuperação de trechos e ficam fora.

## Limites desta avaliação

- **São 59 perguntas (34 + 25).** Uma pergunta muda uma média em 0,02 a 0,03. Diferenças pequenas não devem ser lidas como ranking.
- **Eu escrevi as perguntas e os critérios.** Um critério por expressão regular pode marcar como relevante um trecho que não responde bem, ou deixar de fora um que responde com outras palavras. A precisão@5 é a métrica mais sensível a isso.
- **O embedding é pequeno** (0,6 bilhão de parâmetros, em CPU). Com outro modelo de embedding o denso pode subir.
- **Os índices do RJ e da pesquisa foram reconstruídos** com a ingestão atual (leitura de PDF em duas colunas e cabeçalhos de página). Na pesquisa os trechos saíram idênticos (mesmo SHA-256); no RJ a base de editais foi de 1.458 para 1.456 trechos (versão v2), e as métricas de recuperação não mudaram (MRR@10: BM25 0,687, denso 0,745, RRF 0,722). A v3 dos editais do RJ tem os mesmos 13 arquivos da v2 (mesmo SHA-256 em todos), então não há o que reindexar.

## Decisões

1. **Manter o RRF como busca padrão.** Tem o melhor MRR entre os sistemas de k=60 e um hit@5 (0,94) próximo ao do denso. Não é imune a falha: quando o BM25 não traz nada relevante, ele pode atrapalhar (as duas perguntas acima).
2. **Manter k_rrf = 60.** O sinal a favor do 10 vinha do primeiro conjunto, em que o valor foi escolhido; no conjunto novo (25 perguntas) a diferença some (+0,005).
3. **Manter o filtro de vigência e de doença**, que já é necessário por correção (não misturar versões) e é um efeito firme: +0,064 de MRR nas 59 perguntas, com intervalo sem zero (7 vitórias, 2 derrotas).
4. **Próximos passos para fechar a fase 5:** continuar a rodada de respostas do RJ e da pesquisa com `--retomar` quando o limite da Groq voltar; medir o efeito do rerank na resposta final (a ordenação dos trechos já foi medida). O conjunto de perguntas foi ampliado (segundo conjunto, acima).
6. **Rerank: opcional e desligado.** Melhora o MRR em +0,10 nas 59 perguntas, ao custo de ~19 s por busca em CPU. Sem evidência de ganho na resposta final e com esse custo, não vira o padrão.
5. **Falha conhecida:** o SA-10. A causa (comparar sem ter extraído os medicamentos do protocolo) foi tratada com a tabela `pcdt_medicamento`, mas a pergunta só será dada como corrigida depois de repetida com o modelo, e a lista cobre só o que o protocolo enumera.
