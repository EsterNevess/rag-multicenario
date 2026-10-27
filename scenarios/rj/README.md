# Cenário: contratações públicas do Estado do RJ

**Estado:** planejado (é a próxima fase, junto com o motor).

## O problema

Toda contratação pública brasileira é publicada no PNCP (Lei 14.133/2021). O portal permite consultar contrato por contrato, mas não responde perguntas como "o Estado paga mais caro que os municípios por este item?". Isso exige cruzar um edital em PDF, uma tabela de contratos e valores homologados, quatro documentos de quatro naturezas.

## As quatro bases

| Base | Conteúdo | Fonte | Acesso |
|---|---|---|---|
| Normativa | Lei 14.133/2021, decretos estaduais, editais | Planalto, Rede Log do RJ, PNCP | HTML e PDF por URL |
| Estruturada | Contratos e contratações, UF = RJ | PNCP, API de consulta | REST, paginada |
| Agregada | Plano de Contratações Anual e valores homologados por item | PNCP | REST |
| Conversacional | Atas e trilha de e-mails de compras | PNCP + base sintética | Texto |

Período do corpus: janeiro a agosto de 2026 (a confirmar no inventário da fase 1).

## Arquivos do cenário

| Arquivo | Para quê |
|---|---|
| [corpus_manifest.yaml](corpus_manifest.yaml) | O que entra na base, de onde e como |
| [PERSONAS.md](PERSONAS.md) | Três personas e as perguntas de cada uma |
| [sensibilidade.yaml](sensibilidade.yaml) | O que responder, mascarar e recusar |
| [eval/questions.json](eval/questions.json) | 10 perguntas com rota e fonte esperadas (uma delas é uma falha prevista) |

## Limitações já conhecidas

- A API do PNCP não tem busca textual: a seleção por objeto é feita localmente.
- Preço por unidade de medida exige itens de contratação e normalização de unidades.
- A base conversacional é sintética e marcada como tal.
