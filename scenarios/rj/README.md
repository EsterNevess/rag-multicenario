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

## Base estruturada: a API e o snapshot

A base estruturada (contratações e contratos) tem duas fontes:

- **A API do PNCP** (`contratos_estruturado`): é a fonte oficial e reprodutível. Para não sobrecarregar um serviço público, a coleta pela API cobre **uma amostra de janeiro de 2026** (as contratações do RJ nas 13 modalidades e os contratos de cada órgão encontrado), com 2,5 s entre as requisições.
- **O snapshot** (`snapshot_pncp_rj`): as respostas da API que o projeto em equipe já havia guardado em um banco SQLite, para o **período completo** (janeiro a agosto de 2026: 37.637 contratações e 41.978 contratos). Coloque o banco em `data/manual/snapshot/app.db` ou use `--snapshot-db`. O manifesto traz a impressão digital do conteúdo, e o `fetch` recusa um snapshot diferente.

```bash
python -m src.fetch_corpus update --scenario rj --snapshot-db caminho/do/app.db
```

## Como gerar os trechos

Com o corpus coletado, a ingestão corta cada documento em trechos (um por artigo ou por seção) e grava `data/processed/rj/<versão>_editais_normativo.jsonl`, mais um relatório com a contagem por documento:

```bash
python -m src.ingest --scenario rj
```

Na versão atual: 1.458 trechos (298 da Lei 14.133, 343 dos decretos, 817 dos editais). Cada trecho traz o documento, o artigo ou a seção, a hierarquia (título, capítulo, seção), a data de publicação, a sensibilidade e a marca de dado pessoal mascarado. Os arquivos gerados não vão para o Git.

## Como indexar

A indexação transforma cada trecho em um vetor (com o Ollama, que roda no seu computador, sem conta e sem custo) e monta, para a base, o índice vetorial e o de palavras:

```bash
ollama pull qwen3-embedding:0.6b
python -m src.index build --scenario rj
```

Em um notebook sem placa de vídeo, são cerca de 0,2 a 0,4 trecho por segundo, então a primeira indexação dos 1.458 trechos leva de 1 a 2 horas. Ela pode ser interrompida e retomada: cada lote fica em um cache em disco. Para usar a OpenAI no lugar do Ollama, configure `EMBEDDING_PROVIDER=openai` no `.env` (ver `.env.example`).

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
