# Cenário: inteligência sobre pesquisa acadêmica

**Estado:** em andamento (fase 2). A base estruturada está coletada e normalizada: 5.000 artigos do OpenAlex (os 1.000 mais citados de cada ano, 2020 a 2024, de 10 instituições do RJ). Os 8 documentos normativos (2 resoluções do CNS, 5 documentos da FAPERJ e 1 chamada do CNPq) estão coletados; falta cortá-los e indexá-los. Faltam também a base agregada e a conversacional sintética.

## O problema

A informação sobre pesquisa existe, aberta, em quatro lugares que não conversam: o edital de fomento está em um PDF no site da agência; quem foi financiado está no metadado do OpenAlex; o que foi produzido está no abstract do artigo; e o parecer que aprovou ou rejeitou o projeto está em outra linguagem. A pergunta "o dinheiro deste edital virou produção científica?" é óbvia de fazer e cara de responder.

## As quatro bases

| Base | Conteúdo | Fonte | Acesso |
|---|---|---|---|
| Normativa | Editais de fomento e normas de ética em pesquisa | FAPERJ, CNPq, CNS/CONEP | PDF por URL |
| Estruturada | Trabalhos, autores, instituições, financiamento, citações | OpenAlex | REST |
| Agregada | Indicadores bibliométricos, abstracts e textos | OpenAlex, arXiv, SciELO | REST e texto |
| Conversacional | Pareceres de revisão por pares e correspondência editorial | Sintética | Texto |

## O ponto central: roteamento entre bases próximas

Tudo aqui é "pesquisa". O roteador não pode se apoiar em vocabulário distinto para decidir; precisa raciocinar sobre o **tipo de evidência**. É o cenário mais difícil de rotear e o mais fácil de coletar.

## O ponto de segurança: anonimato da revisão

O sistema nunca revela a identidade de um revisor em revisão cega e não emite juízo sobre a qualidade de um pesquisador. Os pareceres sintéticos trazem pistas plantadas para testar isso. Ver [sensibilidade.yaml](sensibilidade.yaml).

## Arquivos do cenário

| Arquivo | Para quê |
|---|---|
| [corpus_manifest.yaml](corpus_manifest.yaml) | O que entra na base, de onde e como |
| [PERSONAS.md](PERSONAS.md) | Três personas e as perguntas de cada uma |
| [sensibilidade.yaml](sensibilidade.yaml) | O que responder, mascarar e recusar |
| [eval/questions.json](eval/questions.json) | 10 perguntas com rota e fonte esperadas (uma é uma falha prevista) |

## Limitações já conhecidas

- Só a base estruturada foi coletada; os editais e as normas de ética exatos ainda serão escolhidos.
- A amostra são os artigos **mais citados**, não a produção inteira, e inclui colaborações gigantes. Os números das respostas valem para a amostra.
- As bases conversacionais são sintéticas e marcadas como tal.
