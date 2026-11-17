# Dados, modelos e licenças

Este documento diz de onde vêm os dados e os modelos do projeto, sob que licença cada um é usado, e o que **não** foi conferido. Não é parecer jurídico.

## O que o repositório contém e o que não contém

- **Contém:** o código (licença MIT, ver [LICENSE](LICENSE)), os manifestos que dizem o que coletar e de onde (`scenarios/<id>/corpus_manifest.yaml`), os inventários de cada coleta (nome, tamanho e SHA-256 dos arquivos, em `data/inventory/`), as perguntas de avaliação e os geradores dos dados sintéticos.
- **Não contém:** nenhum documento, página, artigo, contrato ou resposta de API de terceiros, nem pesos de modelos. Tudo isso é baixado por quem rodar o projeto (`python -m src.fetch_corpus`) e fica em pastas que o Git ignora (`data/raw`, `data/processed`, `data/index`, `data/normalized`, `data/models`).
- **Citações curtas:** o `DEMO.md` e o `AVALIACAO.md` transcrevem respostas do sistema, que citam artigos de lei e trechos de protocolos em tamanho de uma frase ou uma lista, com a fonte.

## Fontes de dados

| Cenário | Base | Fonte | Licença ou condição declarada no manifesto | Observações |
|---|---|---|---|---|
| RJ | Normativa | Lei 14.133/2021 (Planalto), 7 decretos estaduais do RJ, 5 editais do PNCP | Domínio público | Os decretos e a Lei foram colocados à mão em `data/manual/` (o site de origem bloqueia download automático e o projeto não contorna o bloqueio). |
| RJ | Estruturada | PNCP (Portal Nacional de Contratações Públicas), API de consulta | Dados abertos gov.br, com atribuição | Amostra de janeiro de 2026 pela API. |
| RJ | Estruturada (snapshot) | Respostas da API do PNCP guardadas pelo projeto em equipe (Time 03), de janeiro a agosto de 2026 | Dados abertos gov.br, com atribuição | São dados públicos do PNCP, coletados por outro projeto; o banco não está neste repositório. |
| Saúde | Normativa | PCDT do Ministério da Saúde e da Conitec (8 documentos: diabetes tipo 1 e tipo 2, doença de Parkinson, artrite reumatoide em 5 versões) | Documentos oficiais do Ministério da Saúde | O Relatório Conitec nº 551, que li, traz: "É permitida a reprodução parcial ou total desta obra, desde que citada a fonte e que não seja para venda ou qualquer fim comercial". **Não li essa nota nos outros PDFs.** |
| Saúde | Estruturada | RENAME 2024 (2ª edição), Ministério da Saúde | Documentos oficiais do Ministério da Saúde | Mesma cautela: a condição de uso comercial da RENAME não foi conferida. |
| Pesquisa | Normativa | 2 resoluções do Conselho Nacional de Saúde, 5 documentos da FAPERJ, 1 chamada do CNPq | Documentos públicos de órgãos oficiais | Os termos de reutilização de cada documento não foram conferidos um a um. |
| Pesquisa | Estruturada | OpenAlex: 5.000 artigos (os 1.000 mais citados de cada ano, 2020 a 2024, de 10 instituições do RJ) | CC0 (declarado pela OpenAlex para os dados que distribui) | O resumo de um artigo é obra do autor ou do editor; a OpenAlex o distribui em forma de índice invertido. O projeto não redistribui nada disso. |

**O que é conferível e o que não é.** A coluna de licença é a que escrevi nos manifestos, a partir do que cada fonte declara. Não verifiquei os termos de uso de cada site, e a nota de "sem fins comerciais" que li num documento da Conitec pode valer para os outros. Como o repositório não distribui esses dados, a condição pesa sobre **quem os baixar e usar**, e o código MIT não a remove. Se for usar os dados de saúde em algo comercial, confira os termos de cada documento.

**Atribuição.** O PNCP pede atribuição; o texto de um protocolo ou de uma portaria deve citar o Ministério da Saúde ou a Conitec como fonte; para a OpenAlex, cite a OpenAlex (Priem et al., 2022, arXiv:2205.01833).

## Dados sintéticos

Três bases são **sintéticas**, geradas por `python -m src.synth build` com semente fixa (20261029) e marcadas como tais nas respostas: as atas e e-mails de compras do RJ, a ouvidoria da saúde e os pareceres da pesquisa. Os nomes, telefones, e-mails e documentos que aparecem nelas são inventados. Dois cuidados: (1) os CPFs de teste têm dígitos verificadores válidos e, por coincidência, podem ser de uma pessoa real; (2) os registros trazem de propósito dados pessoais fictícios para testar a redação (ver o AVALIACAO.md, seção sobre redação, que mostra que ela perde mais da metade das frases sensíveis em texto novo e **não serve para dados reais**).

## Modelos

| Modelo | Para quê | Licença | Como é usado |
|---|---|---|---|
| `openai/gpt-oss-120b` | Gerar as respostas e montar o plano da consulta | Pesos sob Apache-2.0 (conforme o repositório do modelo); o uso pela API da Groq segue os termos da Groq | **Via API da Groq (nuvem).** Cada pergunta e os trechos de evidência enviados ao modelo saem do seu computador para o provedor. Os dados do projeto são públicos ou sintéticos; não use dados reais sensíveis. |
| `qwen3-embedding:0.6b` | Vetores de busca | Apache-2.0 (conforme o repositório do modelo) | Local, no Ollama. |
| `BAAI/bge-reranker-v2-m3` (conversão ONNX quantizada de `onnx-community`) | Rerank opcional | Apache-2.0 o modelo original; a conversão não declara licença própria e deriva do original | Local, em `data/models/` (não vai para o Git). |
| `qwen3:4b-instruct-2507-q4_K_M` | Teste de encadeamento sem a Groq (opcional) | Apache-2.0 (texto da licença na camada do registro do Ollama) | Local, no Ollama. |

Provedores opcionais: com `LLM_PROVIDER=openai`, as perguntas e a evidência vão para a OpenAI, sob os termos dela.

## Dependências de código

Licenças lidas nos metadados dos pacotes instalados em 16/11/2026:

| Pacote | Licença |
|---|---|
| pydantic, pydantic-settings, faiss-cpu, onnxruntime, truststore, PyYAML, pytest, ruff | MIT |
| python-dotenv, numpy | BSD-3-Clause (numpy inclui outras permissivas) |
| requests, streamlit, coverage, tokenizers | Apache-2.0 |
| **pymupdf** | **AGPL-3.0 ou licença comercial da Artifex (dupla licença)** |
| pymupdf4llm, pymupdf-layout (instalados junto com o pymupdf 2.0) | não declarada nos metadados que li |

**Atenção ao PyMuPDF.** É a única dependência com licença copyleft forte (AGPL-3.0). O projeto o usa para ler PDFs e **não o redistribui**, então o código MIT daqui não é afetado, mas quem **hospedar o app como um serviço de rede** ou distribuir um produto que o inclua deve avaliar as obrigações da AGPL ou obter a licença comercial. Se isso importar, trocar o leitor de PDF é possível: ele fica isolado em `src/ingest.py` e `src/normalize_saude.py`. Para `pymupdf4llm` e `pymupdf-layout`, confira a licença no projeto de origem antes de redistribuir.

## O que não foi conferido

- Os termos de reutilização de cada PDF e de cada site de origem, um a um.
- Se os resumos do OpenAlex podem ser exibidos fora do contexto de pesquisa.
- A licença de `pymupdf4llm` e `pymupdf-layout`.
- Se a conversão ONNX do reranker herda exatamente a licença do modelo original (a conversão não diz).
