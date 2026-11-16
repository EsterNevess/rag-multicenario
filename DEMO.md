# Roteiro de demonstração

Roteiro para mostrar o sistema nos três cenários. Cada pergunta mostra uma capacidade diferente. As respostas abaixo são saídas reais do sistema, copiadas sem edição, e **não são garantidas**: o modelo muda o texto a cada execução (ver [AVALIACAO.md](AVALIACAO.md)). Antes de gravar, rode cada pergunta e confira.

Comando (em cada cenário): `python -m src.generate --scenario <rj|saude|pesquisa> "<pergunta>"`

Estado deste roteiro: as perguntas da saúde e duas do RJ têm resposta capturada. As demais do RJ e da pesquisa estão **a capturar**, porque o plano gratuito da Groq esgotou o limite diário.

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
| 2 | Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2026? | agregada | cálculo em SQL sobre dados reais do PNCP |
| 3 | Sou microempresa: tenho tratamento diferenciado em licitações e há contratações abertas de limpeza? | híbrida | norma + dados |
| 4 | Esse contrato da Secretaria X foi fraude? | recusa | não acusa ninguém |

Respostas capturadas:

- **1.** "As modalidades de licitação previstas na Lei 14.133/2021 são: I – pregão; II – concorrência; III – concurso; IV – leilão; V – diálogo competitivo. Essas cinco modalidades constam do art. 28 [...] [1]."
- **2, 3, 4:** a capturar.

**Falha conhecida, não gravar sem conferir:** a pergunta "Quais contratações de material de escritório foram publicadas por órgãos do RJ em 2026?" escolheu a rota de dados, mas terminou com "Não consegui escrever uma resposta com fontes verificáveis. Tente reformular a pergunta." Isso mostra que a verificação de citações recusa quando não consegue ligar o texto à evidência. É o comportamento desejado, mas a pergunta não serve de vitrine. A RJ-04 (proporção de contratações diretas) também é instável.

## Pesquisa acadêmica

| # | Pergunta | Rota | Para mostrar |
|---|---|---|---|
| 1 | Quais os 10 trabalhos mais citados entre 2020 e 2024 de instituições do RJ sobre aprendizado de máquina? | estruturada | dados do OpenAlex |
| 2 | Que despesas o Edital FAPERJ 13/2023 permite na prestação de contas? | documentos | edital com citação |
| 3 | Meu estudo usa apenas dados públicos anonimizados. Preciso submeter ao CEP? | documentos | resolução do CNS |
| 4 | Que problemas o revisor 2 aponta no manuscrito M-014? | documentos | base sintética (pareceres) |
| 5 | Quem foi o revisor do manuscrito M-014? | recusa | não revela identidade de revisor |

Respostas: todas a capturar.

**Limite conhecido:** "Desses, quais declaram financiamento da FAPERJ ou do CNPq?" usa a memória de um turno (a pergunta e o plano anteriores), implementada mas **ainda não conferida com o modelo real**. Antes de mostrar, rode a pergunta anterior e a de continuação e confira o resultado.

## Para fechar a captura

Quando o limite diário da Groq voltar, rode as perguntas marcadas "a capturar" e cole as respostas aqui. Não há vídeo: este roteiro é a demonstração. Cole as respostas como saíram, com a linha de fontes que o sistema devolve.
