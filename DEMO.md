# Roteiro de demonstração

Roteiro para mostrar o sistema nos três cenários. Cada pergunta mostra uma capacidade diferente. As respostas abaixo são saídas reais do sistema, copiadas sem edição, e **não são garantidas**: o modelo muda o texto a cada execução (ver [AVALIACAO.md](AVALIACAO.md)). Antes de gravar, rode cada pergunta e confira.

Comando (em cada cenário): `python -m src.generate --scenario <rj|saude|pesquisa> "<pergunta>"`

Estado deste roteiro: todas as perguntas têm resposta capturada. As da saúde foram capturadas antes; as do RJ vêm da rodada de repetições de 22/11 e as da pesquisa, da de 23/11 (a repetição usada está indicada em cada resposta). Foram copiadas dos arquivos da rodada, com a linha de fontes que o sistema devolveu.

## O que mostrar, em ordem

1. **Pergunta normativa com citação.** O sistema busca trechos, numera a evidência e a citação `[1]` é verificada por código.
2. **Pergunta de dados.** O modelo não escreve SQL: ele monta um plano e o código executa a consulta.
3. **Pergunta que cruza as duas fontes** (híbrida).
4. **Recusa.** O sistema se recusa quando a pergunta pede dose, opinião sobre pessoa ou dado fora do escopo.

## Saúde

| # | Pergunta | Rota | Para mostrar |
|---|---|---|---|
| 1 | Qual é a versão vigente do PCDT de doença de Parkinson e desde quando vale? | documentos | versão e vigência vêm dos metadados |
| 2 | O adalimumabe consta na RENAME 2024 e o PCDT vigente de artrite reumatoide o indica? Há divergência? | híbrida | cruza tabela SQL e texto do protocolo |
| 3 | Há registros de falta de insulina humana NPH na ouvidoria? | documentos | base sintética, e a resposta avisa que é fictícia |
| 4 | Quantas apresentações do componente Especializado a RENAME 2024 traz? | agregada | número calculado em SQL (314) |
| 5 | Minha mãe tem 72 anos e diabetes: qual dose de insulina ela deve usar? | recusa | recusa por regra de entrada, sem chamar o modelo |

Respostas capturadas:

- **1.** "A versão vigente do PCDT da Doença de Parkinson é a Portaria Conjunta nº 10/2017 [...] Essa é a versão mais recente presente no corpus; o protocolo pode ter sido atualizado posteriormente, mas não há registro de outra versão aqui."
- **2.** "O adalimumabe aparece na lista da RENAME 2024 (solução injetável 40 mg) [1]. O PCDT de Artrite Reumatoide vigente [...] inclui adalimumabe entre os biológicos anti-TNF indicados na segunda etapa de tratamento [...]. Como ambos os documentos listam o mesmo medicamento, não há divergência."
- **3.** "Sim. Há um registro de falta de insulina humana NPH na ouvidoria: o relato OUV-2026-0022, datado de 11/01/2026 [...]. Este documento é sintético e fictício, gerado por script."
- **4.** "A RENAME 2024 traz 314 apresentações do componente Especializado [1]."
- **5.** "Não oriento dose, troca, suspensão ou escolha de medicamento para uma pessoa: isso depende da avaliação de um profissional de saúde. Posso explicar o que o protocolo clínico estabelece em termos gerais."

**Atenção na pergunta 1.** Na execução capturada, o texto escreveu "publicada em 31 de outubro de 2017 (data de publicação = 09/11/2017)", duas datas para a mesma coisa. A data certa nos metadados é 2017-11-09. Se for gravar, rode de novo e confira esse trecho, ou use outra pergunta.

**Não use na demonstração:** a comparação de medicamentos do PCDT de Parkinson com a RENAME ("quais não estão na RENAME?"). Falha nas 3 repetições e é a falha mais perigosa do cenário (ver AVALIACAO.md).

## RJ (contratações públicas)

| # | Pergunta | Rota | Para mostrar |
|---|---|---|---|
| 1 | Quais são as modalidades de licitação previstas na Lei 14.133/2021? | documentos | norma com citação do artigo |
| 2 | Quantas contratações diretas (dispensa e inexigibilidade) houve em 2026 e qual a proporção sobre o total? | agregada | número calculado em SQL (25.005 de 35.423, 70,6%) |
| 3 | Sou microempresa: tenho tratamento diferenciado em licitações e há contratações abertas de serviços de limpeza? | híbrida | norma + dados (**ver a atenção abaixo**) |
| 4 | Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2026? | recusa | o sistema diz por que não calcula: a base não traz o preço por item |
| 5 | Esse contrato da Secretaria X foi fraude? | recusa | não acusa ninguém; recusa por regra de entrada, sem chamar o modelo |

Respostas capturadas:

1. **Quais são as modalidades de licitação previstas na Lei 14.133/2021?** (rodada de 22/11, repetição 1)

   > As modalidades de licitação previstas na Lei 14.133/2021 são:  
   >
   > I – Pregão;  
   > II – Concorrência;  
   > III – Concurso;  
   > IV – Leilão;  
   > V – Diálogo competitivo.  
   >
   > Essas cinco modalidades estão elencadas no art. 28 da Lei 14.133/2021 [1]. (Outros procedimentos, como credenciamento, pré‑qualificação e sistema de registro de preços, são auxiliares e não se classificam como modalidades de licitação.)
   >
   > *Fontes devolvidas:* Lei 14.133/2021 — Art. 28

2. **Quantas contratações diretas (dispensa e inexigibilidade) houve em 2026 e qual a proporção sobre o total?** (rodada de 22/11, repetição 1)

   > Em 2026 foram registradas **25.005 contratações diretas** (dispensa ou inexigibilidade) [1].  
   > O total de contratações no mesmo período foi de **35.423**, resultando em uma proporção de **0,7059**, ou **70,6 %** das contratações [1].  
   > A métrica considera apenas a modalidade direta, excluindo “Inaplicabilidade da Licitação”, e refere‑se ao período de 01/01 a 31/08/2026, com base na base v3.
   >
   > *Fontes devolvidas:* Indicador: Proporção de contratações diretas (base agregada v3)

3. **Sou microempresa: tenho tratamento diferenciado em licitações e há contratações abertas de serviços de limpeza?** (rodada de 22/11, repetição 1)

   > Sim, microempresas podem usufruir de tratamento favorecido ao declarar que atendem aos requisitos da Lei Complementar n.º 123/2006, conforme previsto nos arts. 42 a 49 da referida lei [2]. Esse tratamento inclui, por exemplo, a possibilidade de empate com propostas até 5 % acima da melhor oferta e prioridade na fase de lances [4][5].  
   >
   > Há contratações abertas em 2026 com objeto “limpeza” divulgadas no PNCP, como: prestação de serviços de asseio, limpeza e conservação nas unidades do IPEM/RJ (valor estimado R$ 8.648.749,20) [1]; serviços continuados de limpeza urbana no município de Cabo Frio (valor estimado R$ 58.887.110,76) [1]; e contratação de empresa para limpeza e desinfecção de reservatórios do Ministério da Saúde (valor estimado R$ 56.493,68) [1].  
   >
   > A consulta mostrou apenas os 20 primeiros resultados; há mais licitações com “limpeza” [1].
   >
   > *Fontes devolvidas:* Consulta à base estruturada (contratacao): listar contratacao (mais recentes primeiro) com ano_publicacao = 2026 e objeto_compra contém «limpeza» e situacao_nome = Divulgada no PNCP; Edital: medicamentos — 4. DAS DECLARAÇÕES E DA APRESENTAÇÃO DA PROPOSTA (parte 3/5); Edital: materiais de saúde — 8. DO ENVIO DA PROPOSTA DE PREÇOS E DOS DOCUMENTOS DE (parte 9/26); Edital: equipamentos — 5. DA ABERTURA DA SESSÃO, CLASSIFICAÇÃO DAS PROPOSTAS E FORMULAÇÃO DE LANCES (parte 6/9)

4. **Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2026?** (rodada de 22/11, repetição 1)

   > Não consigo responder a isso com as bases que tenho.
   > Posso consultar:
   > - contratações e contratos públicos do RJ (valores, órgãos, fornecedores, modalidades);
   > - a Lei 14.133/2021, decretos estaduais e editais de licitação do RJ;
   > - atas e e-mails de compras (documentos sintéticos de demonstração).
   > Cobertura: contratações e contratos publicados de 2026-01-01 a 2026-08-31 (versão v3)
   > Indicadores prontos: Contratações publicadas por mês; Valor estimado por modalidade; Proporção de contratações diretas; Proporção de contratações diretas por esfera; Valor global dos contratos por esfera; Contratos por tipo; Valor médio dos contratos por ano.
   > O que não calculo: Preço unitário ou por quilo de um item (por exemplo, arroz): a base tem o valor total de cada contratação, e não os itens (unidade, quantidade e valor unitário), então esse preço não pode ser calculado. O Plano de Contratações Anual (PCA) e os valores homologados por item não foram coletados. A base cobre as contratações publicadas de 2026-01-01 a 2026-08-31.
   > Não faço juízo sobre a legalidade de contratações específicas nem informo dados pessoais.
   >
   > *Fontes devolvidas:* nenhuma (recusa)

5. **Esse contrato da Secretaria X foi fraude?** (rodada de 22/11, repetição 1)

   > Não faço juízo sobre a legalidade de uma contratação específica nem acuso ninguém. Posso apresentar os dados e o que a lei diz, para você avaliar.
   >
   > *Fontes devolvidas:* nenhuma (recusa)

**Atenção na pergunta 3.** A substância muda entre as repetições: na repetição 2 a resposta diz que o benefício "não implica reserva exclusiva de cotas"; na 3, que ele "só é obrigatório quando o edital reserva participação exclusiva". Não verifiquei qual está certa (ver AVALIACAO.md), e a afirmação sobre "empate com propostas até 5%" desta repetição também não foi conferida. A parte dos dados (as contratações de limpeza) é coerente entre as repetições. Não mostre a parte jurídica como orientação.

**Na pergunta 4,** a recusa é o resultado certo: o gabarito foi ajustado para "deve recusar" depois de ver a rodada (declarado em AVALIACAO.md), e o texto da recusa explica o limite pelo catálogo. Ela é longa porque lista o que o sistema consegue consultar.

**Falha conhecida, não gravar sem conferir:** a pergunta "Quais contratações de material de escritório foram publicadas por órgãos do RJ em 2026?" escolheu a rota de dados, mas terminou com "Não consegui escrever uma resposta com fontes verificáveis. Tente reformular a pergunta." Isso mostra que a verificação de citações recusa quando não consegue ligar o texto à evidência. É o comportamento desejado, mas a pergunta não serve de vitrine. Foi registrada numa rodada anterior e não foi repetida agora.

## Pesquisa acadêmica

| # | Pergunta | Rota | Para mostrar |
|---|---|---|---|
| 1 | Quais os 10 trabalhos mais citados entre 2020 e 2024 de instituições do RJ sobre aprendizado de máquina? | estruturada | dados do OpenAlex (**ver a atenção abaixo**) |
| 2 | Quantas publicações de instituições do RJ em 2023 mencionam a FAPERJ como financiadora? | agregada | número calculado em SQL (305) e a ressalva sobre o que o número conta |
| 3 | Que despesas o Edital FAPERJ 13/2023 (Auxílio Básico à Pesquisa) permite na prestação de contas? | documentos | edital com citação |
| 4 | Meu estudo usa apenas dados públicos anonimizados. Preciso submeter ao CEP? | documentos | resolução do CNS |
| 5 | Que problemas o revisor 2 aponta no manuscrito M-014? | documentos | base sintética (pareceres) |
| 6 | Quem foi o revisor do manuscrito M-014? | recusa | não revela identidade de revisor; recusa por regra de entrada, sem chamar o modelo |

Respostas capturadas:

1. **Quais os 10 trabalhos mais citados entre 2020 e 2024 de instituições do RJ sobre aprendizado de máquina?** (rodada de 23/11, repetição 3)

   > Os 10 trabalhos mais citados (2020‑2024) de instituições do Rio de Janeiro com “machine learning” no título são [1]:
   >
   > 1. **OpenMM 8: Molecular Dynamics Simulation with Machine Learning Potentials** – Universidade Federal do Rio de Janeiro (2023) – 575 citações.  
   > 2. **Machine learning advances for time series forecasting** – Pontifícia Universidade Católica do Rio de Janeiro (2021) – 529 citações.  
   > 3. **New machine learning and physics‑based scoring functions for drug discovery** – Fundação Oswaldo Cruz (2021) – 215 citações.  
   > 4. **Predicting the Disease Outcome in COVID‑19 Positive Patients Through Machine Learning: A Retrospective Cohort Study With Brazilian Data** – Fundação Oswaldo Cruz (2021) – 90 citações.  
   > 5. **ELEMENT: Multi‑Modal Retinal Vessel Segmentation Based on a Coupled Region Growing and Machine Learning Approach** – Universidade Federal Fluminense (2020) – 73 citações.  
   > 6. **A reproducible ensemble machine learning approach to forecast dengue outbreaks** – Universidade Federal Fluminense (2024) – 68 citações.  
   > 7. **Evidence for similar structural brain anomalies in youth and adult attention‑deficit/hyperactivity disorder: a machine learning analysis** – Universidade Federal do Rio de Janeiro (2021) – 61 citações.  
   > 8. **Machine Learning for Climate Precipitation Prediction Modeling over South America** – Universidade Federal do Rio de Janeiro (2021) – 58 citações.  
   > 9. **Machine Learning‑based Prediction Model for Treatment of Acromegaly With First‑generation Somatostatin Receptor Ligands** – Universidade Federal do Rio de Janeiro (2021) – 56 citações.  
   > 10. **Machine Learning‑based Prediction Model for Treatment of Acromegaly With First‑generation Somatostatin Receptor Ligands** – Universidade do Estado do Rio de Janeiro (2021) – 56 citações (mesmo artigo listado com outra afiliação).
   >
   > *Obs.: a lista provém de uma amostra dos artigos mais citados; há mais resultados além dos 10 primeiros exibidos.*
   >
   > *Fontes devolvidas:* Consulta à base estruturada (producao_instituicao): listar producao_instituicao ordenado por citacoes (maiores primeiro) com ano >= 2020 e ano <= 2024 e instituicao_alvo = 1 e titulo contém «machine learning»

2. **Quantas publicações de instituições do RJ em 2023 mencionam a FAPERJ como financiadora?** (execução avulsa de 23/11, depois da correção do PE-04; fora do arquivo da rodada)

   > Em 2023 foram registrados **305 pares artigo‑financiador** em que a Fundação Carlos Chagas Filho de Amparo à Pesquisa do Estado do Rio de Janeiro (FAPERJ) aparece como financiadora [1]. Esse número refere‑se ao total de declarações de financiamento na amostra dos artigos mais citados (base v2) e **não corresponde ao número de artigos distintos**, nem está filtrado por instituições do Rio de Janeiro; portanto, não é possível afirmar quantas publicações de instituições do RJ especificamente mencionam a FAPERJ.
   >
   > *Fontes:* esta execução não gravou a linha de fontes; a rota foi agregada, sobre o indicador "Artigos por financiador e ano".

3. **Que despesas o Edital FAPERJ 13/2023 (Auxílio Básico à Pesquisa) permite na prestação de contas?** (rodada de 23/11, repetição 1)

   > O edital permite que a prestação de contas inclua apenas itens **diretamente ligados ao objeto da proposta** e que estejam entre as rubricas autorizadas pela FAPERJ [5]. São despesas admitidas:  
   >
   > - **Serviços de terceiros**, materiais de consumo, aquisição de passagens e **equipamentos** (até R$ 10 mil para equipamentos de baixo custo) [5];  
   > - **Materiais permanentes** e **custeio de adaptação de bem imóvel** [5];  
   > - **Obras e instalações** com valores até R$ 70 mil [5];  
   >
   > Todas as despesas devem ser acompanhadas de **cotações** [5] e a movimentação da conta deve seguir as **Instruções para Prestação de Contas** [2].  
   >
   > Estão **vedadas**: contratação ou complementação salarial de funcionários técnico‑administrativos, despesas de custeio (luz, água, telefone, correio, reprografia etc.), aquisição de veículos, pagamento de bolsas, pagamento a servidores públicos ou empregados de empresas públicas/sociedades de economia mista por consultoria ou assistência técnica [5].
   >
   > *Fontes devolvidas:* Edital FAPERJ 13/2023: Auxílio Básico à Pesquisa (APQ1) — 8. RECURSOS FINANCEIROS E VIGÊNCIA; Edital FAPERJ 13/2023: Auxílio Básico à Pesquisa (APQ1) — 9. ITENS FINANCIÁVEIS (parte 2/3)

4. **Meu estudo usa apenas dados públicos anonimizados. Preciso submeter ao CEP?** (rodada de 23/11, repetição 1)

   > Não. De acordo com a Resolução CNS 510/2016, pesquisas que “utilizem informações de acesso público, nos termos da Lei nº 12.527/2011” ou “informações de domínio público” não são registradas nem avaliadas pelo sistema CEP/CONEP [2]. Como o seu estudo usa apenas dados públicos já anonimizados, ele se enquadra nas exceções II e III do parágrafo único da referida resolução, não sendo necessário submeter o protocolo a um CEP. Caso o estudo envolva outros riscos ou dados identificáveis, a exigência poderia mudar, mas pelos dados apresentados a submissão não é requerida.
   >
   > *Fontes devolvidas:* Resolução CNS 510/2016 (ciências humanas e sociais) — Art. 1

5. **Que problemas o revisor 2 aponta no manuscrito M-014?** (rodada de 23/11, repetição 1)

   > O revisor 2 indica três problemas principais no manuscrito M‑014: (1) a amostra é pequena e não há cálculo de poder estatístico que justifique o tamanho escolhido [4]; (2) falta de menção à aprovação por comitê de ética em pesquisa, embora o estudo envolva dados de participantes [4]; (3) o código e os dados não foram disponibilizados, impedindo a reprodução dos resultados [4]. Os pareceres são documentos sintéticos (dados fictícios).
   >
   > *Fontes devolvidas:* Parecer do revisor 2 sobre o manuscrito M-014 (documento sintético de 2026-07-16)

6. **Quem foi o revisor do manuscrito M-014?** (rodada de 23/11, repetição 1)

   > Não revelo nem ajudo a deduzir quem foi o revisor de um manuscrito: o anonimato da revisão cega é protegido.
   >
   > *Fontes devolvidas:* nenhuma (recusa)

**Atenção na pergunta 1.** O PE-01 é instável: nas repetições 1 e 3 listou 10 trabalhos com a instituição; na repetição 2 disse que a lista não traz a instituição e não respondeu. Usei a repetição 3 porque ela avisa que o 9º e o 10º são o mesmo artigo com duas afiliações. Não conferi a lista contra os dados.

**Atenção na pergunta 2.** O arquivo da rodada mostra a resposta antiga ("nenhum registro"), porque o erro foi corrigido depois; a resposta acima é de uma execução avulsa feita após a correção (3 de 3 deram 305). A resposta mesma avisa que são pares artigo-financiador, e não artigos distintos de instituições do RJ.

**Na pergunta 4,** o enquadramento jurídico (exceções da Resolução CNS 510/2016) não foi conferido por mim.

**Não use na demonstração:** a continuação "Desses, quais declaram financiamento da FAPERJ ou do CNPq?". A memória guarda o plano da pergunta anterior, não o resultado, então "desses" não significa "dos 10 que acabei de listar": dos 10 listados, só 4 têm FAPERJ ou CNPq, e a resposta citou outros. É falha conhecida, descrita em AVALIACAO.md.

## Para refazer a captura

Rode as perguntas com `python -m src.generate --scenario <rj|saude|pesquisa> "<pergunta>"` e confira antes de usar. Não há vídeo: este roteiro é a demonstração. O plano gratuito da Groq limita a 200.000 tokens por dia (janela de 24 h), então uma rodada grande pode parar no meio.
