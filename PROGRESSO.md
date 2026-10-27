# Progresso

## Fase 1: motor + cenário do RJ (em andamento)

Plano de passos, cada um com testes e um commit:

- [x] **1.1 Base do motor:** `config.py`, `src/schema.py` (trecho, plano da pergunta, resposta), `src/obs.py` (logs JSONL por estágio, com tokens), `src/pii.py` (mascaramento de CPF, e-mail e telefone) e testes unitários e de contrato (51 passam).
- [ ] 1.2 `fetch_corpus`: `fetch`, `update` e `inventory` para o RJ, com versões imutáveis, inventário e diff.
- [ ] 1.3 `ingest`: chunking por artigo, metadados, sensibilidade e mascaramento.
- [ ] 1.4 `index`: embeddings com cache, FAISS e FTS5 por base.
- [ ] 1.5 `retrieve`: BM25, FAISS e RRF, por base.
- [ ] 1.6 `router`: plano validado contra a tabela de capacidades, guardrails e consulta SQL controlada.
- [ ] 1.7 `generate`, demonstração por linha de comando e README do cenário.

Observação do passo 1.1: o mascaramento de telefone só age com um sinal claro (DDD entre parênteses, celular com 9 na frente ou uma palavra como "tel" antes). Um número "NNNN-NNNN" sozinho é ambíguo em contratações ("2025-2026", "Edital 1234-5678"), e mascará-lo estragaria os textos.

## Fase 0: desenho (concluída)

Pronto:

- [x] Estrutura do repositório e arquivos de configuração (`.gitignore`, `.env.example`).
- [x] README com o estado real dos cenários.
- [x] ARQUITETURA com pipeline, estágios, decisões e módulos.
- [x] Cenário RJ: manifesto, personas, sensibilidade e 10 perguntas de avaliação.
- [x] Cenário saúde: manifesto, personas, sensibilidade e 10 perguntas de avaliação.
- [x] Cenário pesquisa: manifesto, personas, sensibilidade e 10 perguntas de avaliação.

Falta para fechar a fase 0:

- [ ] Revisar os textos e tornar o repositório público.

## Próximas fases

1. **Fase 1:** motor + cenário RJ (normativa e estruturada), com `fetch_corpus`, `ingest`, `index`, `router`, `retrieve`, `generate`, logs e testes. Reaproveita dados públicos já coletados (corpus normativo e snapshot RAW do PNCP), declarados no manifesto.
2. **Fase 2:** cenário de pesquisa acadêmica (coleta por APIs abertas).
3. **Fase 3:** cenário de saúde (extração de PDF um a um, com OCR para os escaneados).
4. **Fase 4:** agregada e conversacional sintética de cada cenário.
5. **Fase 5:** avaliação (ablação BM25 x FAISS x RRF x RRF + rerank, 3 repetições por pergunta) e documento de decisões.

## Decisões tomadas

| Data | Decisão | Motivo |
|---|---|---|
| 2026-10-27 | Um repositório só, com `scenarios/<nome>/` | O motor é o que mostra engenharia; três repositórios copiariam o motor três vezes |
| 2026-10-27 | Nome `rag-multicenario` | Curto e coerente com o `rag-contratacoes` |
| 2026-10-27 | Provedor de modelos configurável (OpenAI ou Ollama) | Não depender do endpoint compartilhado da residência |
| 2026-10-27 | Embeddings de 1024 dimensões | Compatível com o modelo usado no projeto em equipe |
| 2026-10-27 | Dados nunca vão para o Git; manifesto e inventário vão | Reprodutibilidade sem versionar arquivos grandes |
