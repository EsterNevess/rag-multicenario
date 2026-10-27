# Cenário: contratações públicas do Estado do RJ

**Estado:** em andamento. A base normativa foi coletada (v1, 13 arquivos). As outras três bases ainda não.

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

## Como coletar o corpus

A coleta é feita pelo `fetch_corpus`, a partir do [manifesto](corpus_manifest.yaml):

```bash
python -m src.fetch_corpus fetch --scenario rj --versao v1 --base editais_normativo
```

O resultado fica em `data/raw/rj/v1/` (não vai para o Git). O que vai para o Git é o inventário: [CORPUS_INVENTORY.md](CORPUS_INVENTORY.md) e `data/inventory/rj/v1.json`.

**Documentos que precisam ser colocados à mão.** Oito dos 13 documentos não podem ser baixados por script:

- Os 7 decretos estaduais: o site de origem bloqueia download automático (HTTP 403). Este projeto não contorna o bloqueio.
- A Lei 14.133/2021: o site do Planalto não respondeu a requisições automáticas a partir da rede usada na coleta.

Baixe-os no navegador, a partir das origens listadas no manifesto, e coloque em `data/manual/editais_normativo/` (pasta ignorada pelo Git). O `fetch` confere cada arquivo pelo SHA-256 declarado no manifesto e recusa o que for diferente.

Os 5 editais vêm do PNCP e são baixados por script. O servidor de arquivos do PNCP responde HTTP 422 de forma intermitente, e por isso o `fetch` repete com espera crescente.

## Arquivos do cenário

| Arquivo | Para quê |
|---|---|
| [corpus_manifest.yaml](corpus_manifest.yaml) | O que entra na base, de onde e como |
| [PERSONAS.md](PERSONAS.md) | Três personas e as perguntas de cada uma |
| [sensibilidade.yaml](sensibilidade.yaml) | O que responder, mascarar e recusar |
| [CORPUS_INVENTORY.md](CORPUS_INVENTORY.md) | O que foi coletado, com tamanho e SHA-256 de cada arquivo |
| [eval/questions.json](eval/questions.json) | 10 perguntas com rota e fonte esperadas (uma delas é uma falha prevista) |

## Limitações já conhecidas

- A API do PNCP não tem busca textual: a seleção por objeto é feita localmente.
- Preço por unidade de medida exige itens de contratação e normalização de unidades.
- A base conversacional é sintética e marcada como tal.
