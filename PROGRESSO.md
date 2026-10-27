# Progresso

## Fase 1: motor + cenário do RJ (em andamento)

Plano de passos, cada um com testes e um commit:

- [x] **1.1 Base do motor:** `config.py`, `src/schema.py` (trecho, plano da pergunta, resposta), `src/obs.py` (logs JSONL por estágio, com tokens), `src/pii.py` (mascaramento de CPF, e-mail e telefone) e testes unitários e de contrato (51 passam).
- [x] **1.2 `fetch_corpus`:** `fetch`, `update` e `inventory`, com versões imutáveis, inventário (JSON e Markdown) e diff entre versões. Coletada a base normativa do RJ (v1: 13 arquivos, 8,5 MB, 0 falhas). A v2, criada com `update`, tem 13 arquivos idênticos à v1 (diff em `data/inventory/rj/DIFF_v1_v2.md`). 81 testes passam.
- [x] **1.3 `ingest`:** extrai o texto (PDF e HTML), corta por artigo (Lei e decretos) ou por seção numerada (editais), com metadados de vigência, sensibilidade e mascaramento de dados pessoais. Gera **1.458 trechos** da base normativa do RJ (298 da Lei 14.133, 343 dos 7 decretos, 817 dos 5 editais), sem avisos. 117 testes passam.
- [ ] 1.4 `index`: embeddings com cache, FAISS e FTS5 por base.
- [ ] 1.5 `retrieve`: BM25, FAISS e RRF, por base.
- [ ] **1.5b Base estruturada do RJ:** validar o `rest_api` contra a API do PNCP, descobrir como restringir ao RJ (parâmetro de UF ou seleção por órgão) e coletar. Precisa existir antes do 1.6, que consulta essa base por SQL.
- [ ] 1.6 `router`: plano validado contra a tabela de capacidades, guardrails e consulta SQL controlada.
- [ ] 1.7 `generate`, demonstração por linha de comando e README do cenário.

Observações do passo 1.2:

- O servidor de arquivos do PNCP responde HTTP 422 ("erro ao carregar arquivo") de forma **intermitente**: o mesmo link falhou e funcionou em tentativas seguidas. O `fetch` repete falhas passageiras (422 desse tipo, 429, 5xx, queda de conexão) com espera crescente e **não** repete 404 nem 403.
- O site dos decretos do RJ bloqueia download automático (403) e o do Planalto não respondeu a requisições automáticas. Nenhum dos dois foi contornado: os 8 documentos entram como arquivos locais, **conferidos pelo SHA-256** do manifesto.
- O PNCP entrega PDF com `Content-Type: application/octet-stream`. A extensão é decidida pelo conteúdo do arquivo (um PDF começa com `%PDF-`). Esse defeito só apareceu na coleta real, porque o servidor dos testes informava o tipo certo, e foi corrigido com um teste novo.
- O tipo de acesso `rest_api` (páginas JSON, retomável) está implementado e testado contra um servidor local. Contra o PNCP de verdade só houve sondagens: a API exige datas no formato `yyyyMMdd` (com `2026-01-01` responde 422), e sem filtro de UF devolve contratos de todo o país (um veio do Ceará). **Não foi verificado** se existe um parâmetro de UF. A coleta completa fica para o passo 1.5b.
- Bases com `coletar: false` no manifesto (estruturada, agregada e conversacional) não fazem nenhuma requisição e aparecem no inventário como "nao_coletada", com o motivo.

Observações do passo 1.3 (cada uma só apareceu ao olhar o resultado real):

- **Ordem de leitura do PDF.** Nos decretos salvos pelo navegador, o rótulo "Art. 5º" fica em um bloco separado do texto do artigo, e a leitura padrão os entrega embaralhados (no decreto 48.820, só 4 de 20 artigos eram reconhecidos). A leitura ordenada por posição na página (`sort=True`) resolve: agora todos os artigos de todos os decretos são detectados (38/38, 53/53, 66/66, 37/37, 20/20, 38/38 e 18/18).
- **Quebra de linha no HTML da Lei.** O Planalto escreve "Art." e "75." separados por uma quebra no código-fonte, que em HTML vale um espaço. Tratada como linha nova, ela escondia 73 artigos, inclusive o Art. 75 (dispensa). Hoje os 194 artigos estão presentes.
- **CPF só com dígito verificador.** A regra "11 dígitos é CPF" apagava 50 códigos de produto de uma tabela de edital. Agora um CPF sem pontuação só é mascarado se o cálculo do módulo 11 fechar; com a pontuação típica (123.456.789-09), sempre. Trechos afetados caíram de 65 para 16.
- **E-mail funcional de órgão público fica visível.** Domínios `.gov.br`, `.leg.br`, `.jus.br`, `.mp.br` e `.mil.br` são contato institucional público. Os demais são mascarados.
- **Cabeçalho de seção.** Um cabeçalho como "7. DA PARTICIPAÇÃO" seguido de texto comum era colado ao texto e a seção passava despercebida. E "Seção I" com inicial maiúscula não abria um nível da hierarquia.
- **Limites.** Tabelas dos editais são lidas como texto linear (sem estrutura de linhas e colunas). Um decreto que altera outro cita, entre aspas, artigos do decreto alterado, que viram trechos extras (com id `-r2`). Os cabeçalhos "TÍTULO", "CAPÍTULO" e "Seção" vêm juntos com o título na mesma linha.

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
