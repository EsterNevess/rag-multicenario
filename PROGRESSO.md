# Progresso

## Fase 1: motor + cenário do RJ (em andamento)

Plano de passos, cada um com testes e um commit:

- [x] **1.1 Base do motor:** `config.py`, `src/schema.py` (trecho, plano da pergunta, resposta), `src/obs.py` (logs JSONL por estágio, com tokens), `src/pii.py` (mascaramento de CPF, e-mail e telefone) e testes unitários e de contrato (51 passam).
- [x] **1.2 `fetch_corpus`:** `fetch`, `update` e `inventory`, com versões imutáveis, inventário (JSON e Markdown) e diff entre versões. Coletada a base normativa do RJ (v1: 13 arquivos, 8,5 MB, 0 falhas). A v2, criada com `update`, tem 13 arquivos idênticos à v1 (diff em `data/inventory/rj/DIFF_v1_v2.md`). 81 testes passam.
- [x] **1.3 `ingest`:** extrai o texto (PDF e HTML), corta por artigo (Lei e decretos) ou por seção numerada (editais), com metadados de vigência, sensibilidade e mascaramento de dados pessoais. Gera **1.458 trechos** da base normativa do RJ (298 da Lei 14.133, 343 dos 7 decretos, 817 dos 5 editais), sem avisos. 117 testes passam.
- [x] **1.4 `index` (código):** embeddings por Ollama local ou OpenAI, cache em disco, índice FAISS e FTS5 por base, com recusa de índice incompatível. 27 testes. **O índice real da normativa (1.458 trechos) está sendo gerado**; ver as observações abaixo.
- [x] **1.5 `retrieve`:** BM25 (FTS5), busca por vetores (FAISS), fusão RRF, filtros por metadado (inclusive `vigente_em`) e busca por base com garantia de representação. 22 testes. 166 testes no total. O rerank por cross-encoder fica para a fase de avaliação.
- [x] **1.5b/1.5c Base estruturada do RJ:** coleta v3 concluída (542 arquivos, 167 MB, 0 falhas) e normalizada: **37.637 contratações e 46.450 contratos** de 528 órgãos, de 2026-01-01 a 2026-08-31, 0 rejeitados. A amostra da API (janeiro) está inteiramente contida no snapshot do grupo: das 2.710 contratações da API, as 2.710 existem no snapshot (58 com conteúdo atualizado desde então), e a API acrescentou 4.472 contratos que o snapshot não tinha.
- [x] **1.6 `router`:** guardrail de entrada (injeção de prompt, comandos SQL, dados bancários e credenciais, juízo de legalidade, CPF), validação do plano contra a tabela de capacidades e consulta SQL controlada, só leitura. 55 testes; 260 no total. Falta ligar ao analisador (LLM) no 1.7.
- [~] **1.7 `generate`:** analisador (a LLM devolve o plano JSON; plano inválido volta a ela com a lista de problemas, uma vez), execução das rotas estruturada, documentos, híbrida e catálogo, geração com citações [n] conferidas por código, linha de comando (`python -m src.generate`). 21 testes com modelo falso; 281 no total. **Falta:** a rodada real com um modelo (precisa da chave) e a normalização da coleta v3.

Observações do passo 1.2:

- O servidor de arquivos do PNCP responde HTTP 422 ("erro ao carregar arquivo") de forma **intermitente**: o mesmo link falhou e funcionou em tentativas seguidas. O `fetch` repete falhas passageiras (422 desse tipo, 429, 5xx, queda de conexão) com espera crescente e **não** repete 404 nem 403.
- O site dos decretos do RJ bloqueia download automático (403) e o do Planalto não respondeu a requisições automáticas. Nenhum dos dois foi contornado: os 8 documentos entram como arquivos locais, **conferidos pelo SHA-256** do manifesto.
- O PNCP entrega PDF com `Content-Type: application/octet-stream`. A extensão é decidida pelo conteúdo do arquivo (um PDF começa com `%PDF-`). Esse defeito só apareceu na coleta real, porque o servidor dos testes informava o tipo certo, e foi corrigido com um teste novo.
- O tipo de acesso `rest_api` (páginas JSON, retomável) está implementado e testado contra um servidor local. Contra o PNCP de verdade só houve sondagens: a API exige datas no formato `yyyyMMdd` (com `2026-01-01` responde 422), e sem filtro de UF devolve contratos de todo o país (um veio do Ceará). **Não foi verificado** se existe um parâmetro de UF. A coleta completa fica para o passo 1.5b.
- Bases com `coletar: false` no manifesto (estruturada, agregada e conversacional) não fazem nenhuma requisição e aparecem no inventário como "nao_coletada", com o motivo.

Passo 1.7 (geração), decisões:

- **Citação conferida por código.** A resposta tem de citar [n] e cada n precisa existir nas evidências. Citação inexistente ou ausente faz o modelo refazer uma vez; se persistir, o sistema recusa em vez de entregar uma resposta sem fonte verificável. Citações inventadas que sobram são removidas do texto.
- **O trecho restrito nunca chega ao modelo.** A busca de documentos filtra por `sensitivity = publico` e por vigência; há um teste que falha se o filtro for removido (conferido por mutação).
- **Ambientes incompletos são ditos, não escondidos.** Sem a base estruturada ou sem o índice de normas, o analisador só enxerga as rotas disponíveis e um plano para base ausente é recusado pelo validador.
- **Sem acesso ao modelo, falha clara.** Chave ausente, recusada (401), servidor fora do ar ou limite de tentativas viram `LLMIndisponivel` com a instrução de correção, sem repetir a chave na mensagem.
- **Ainda não medido:** a qualidade das respostas e do analisador com um modelo de verdade. Conferir se os números do texto batem com a evidência (além das citações) fica para a avaliação.

Passo 1.6 (roteador), decisões que valem registrar:

- **A LLM nunca escreve SQL.** O plano só traz nomes de campo e valores. O SQL é montado em `src/router.py` com nomes tirados da tabela de capacidades e valores sempre como parâmetro; a conexão abre o banco em modo só leitura (`mode=ro` e `query_only`) e cancela consultas que passem de 10 s. Um teste manda `x'; DROP TABLE ...` como valor e confere que vira texto comum.
- **Tabela de capacidades por entidade.** Cada campo declara tipo, se aceita filtro, se aceita soma ou média e se aceita agrupamento. Soma de texto, filtro em campo inventado ou data fora de AAAA-MM-DD viram `PlanoInvalido`, que lista todos os problemas de uma vez, para o analisador poder corrigir o plano.
- **Resultado que não engana.** Médias e somas vêm com a contagem de valores realmente considerados (um valor homologado vazio fica fora da média e isso aparece), e listas cortadas no limite avisam que há mais.
- **Guardrail com cuidado para não bloquear demais.** A primeira versão recusava qualquer pergunta com a palavra "fraude", o que barraria "qual o procedimento para apurar fraude em licitação segundo a lei?". Ficaram só os padrões de acusação ("houve fraude", "confirme a fraude"). Há testes dos dois lados: o que deve ser recusado e o que deve passar.
- **Limite conhecido:** a rota agregada (PCA) ainda não existe; o plano que a pedir é recusado com mensagem clara.

Passo 1.5c (normalizador): `python -m src.normalize --scenario rj` lê as páginas JSON das bases estruturadas (API e snapshot) e grava `data/normalized/<cenário>/<versão>.sqlite` com as tabelas `contratacao`, `contrato`, `orgao` e `metadados`, mais um `.relatorio.json`.

- **Uma linha por registro.** Se o mesmo `numeroControlePNCP` vem da API e do snapshot, vale o de `dataAtualizacaoGlobal` mais recente; em empate, a API (fonte oficial). A coluna `fonte` guarda a origem (por exemplo `api+snapshot`) e o relatório conta quantos se repetem e quantos diferem no conteúdo.
- **Dados pessoais.** CPF de fornecedor pessoa física vira `[CPF]` (o nome de quem contrata com o poder público é público, o CPF não precisa ser); textos livres passam pelo mascaramento comum do projeto.
- **Nada some sem aviso.** Registro sem número de controle válido é rejeitado e contado no relatório, com exemplos.
- **Teste em escala real** (só o snapshot): 37.637 contratações e 41.978 contratos, 528 órgãos, zero rejeitados, 1.597 CPFs mascarados e nenhum CPF cru no banco, em 13 s. Só 41,5% dos contratos encontram a contratação na própria base: o snapshot guarda contratos de compras feitas em outras épocas ou fora do RJ. Isso limita consultas que cruzam contrato e contratação, e o roteador (1.6) deve tratar o cruzamento como opcional.

Observações do passo 1.5b (API do PNCP, sondada em 2026-10-28):

- **Importação do snapshot (novo tipo de acesso `snapshot_sqlite`).** Lê as tabelas de um banco SQLite só para leitura e grava páginas JSON no mesmo formato da API, então a normalização tratará as duas origens do mesmo jeito. Em escala real: 79.615 registros (37.637 contratações e 41.978 contratos) em 160 páginas, 147 MB, em 5,9 segundos. A identidade do snapshot é a **impressão digital do conteúdo** (hash dos hashes de cada registro, em ordem fixa), gravada no manifesto, e não o hash do arquivo de 700 MB, que muda a cada uso do banco. Os nomes de tabela e coluna do manifesto são validados (nada de SQL solto). O teste da ordem fixa foi conferido por mutação: sem o `ORDER BY`, ele falha.
- **Uma decisão que custou uma coleta parada.** Uma versão é imutável e amarrada ao hash do manifesto. A coleta da v3 já estava rodando quando decidi acrescentar o snapshot ao manifesto; isso deixaria a v3 com um manifesto antigo e obrigaria a refazer a coleta da API depois. Parei a coleta (cerca de 10 minutos de requisições) e apaguei a v3 parcial, que nunca foi publicada, para rodar uma vez só com o manifesto final. Lição: escrever tudo o que muda o manifesto antes de lançar uma coleta longa em um serviço público.

- **A coleta aprendeu a iterar.** Um endpoint pode declarar `iterar` (uma coleta para cada valor de um parâmetro, como as 13 modalidades) e `por_orgao_de` (uma coleta para cada órgão que outro endpoint devolveu). Consulta sem registros fica marcada como "vazio" e não é repetida na execução seguinte, e não deixa pasta vazia.
- **Teste intermitente achado e corrigido no código.** Rodando a suíte inteira, 3 testes falharam uma vez e passaram na seguinte. Repeti até reproduzir: `PermissionError: [WinError 5] Acesso negado` ao renomear a pasta temporária do índice. No Windows, o antivírus segura por um instante os arquivos recém-gravados. Não era só um problema de teste: aconteceria com quem rodasse `python -m src.index build`, no fim de uma indexação longa. A troca de pasta agora é repetida com espera crescente, e há um teste que simula a recusa. Depois da correção: 5 rodadas seguidas, 175 testes, nenhuma falha. Minha primeira hipótese (a virada da meia-noite) estava errada.

- **`uf` existe em `/v1/contratacoes/publicacao`** e funciona (a amostra veio toda do RJ), mas o endpoint exige `codigoModalidadeContratacao`, então é uma consulta por modalidade (1 a 13). **`/v1/contratos` não tem filtro de UF**: só `cnpjOrgao` e `codigoUnidadeAdministrativa`. Isso corrige a nota anterior, que dizia que a existência do parâmetro "não foi verificada".
- **Volume (contratações do RJ, jan. a ago. de 2026):** as modalidades que responderam somam 10.622 (Inexigibilidade 9.127, Concorrência eletrônica 967, Credenciamento 452, Concorrência presencial 45, Leilão eletrônico 24, e poucas nas demais). Dispensa e Pregão (as maiores) foram barradas na sondagem; o snapshot do grupo indica algo como 16 mil de Dispensa. A ordem de grandeza total é de 35 a 40 mil contratações.
- **A API limita o ritmo (HTTP 429).** Uma rajada de 14 requisições, com 1 s entre elas, foi barrada em 4 delas. O `fetch_corpus` agora respeita o `Retry-After` do servidor e, sem ele, espera de 30 a 120 s. Uma consulta sem registros responde **HTTP 204** (corpo vazio), tratada como "acabou" e não como falha.
- Datas no formato `yyyyMMdd`; `tamanhoPagina` é opcional.

Observações dos passos 1.4 e 1.5:

- **Embeddings locais são lentos em CPU.** Com o Ollama e o `qwen3-embedding:0.6b` (639 MB, 1024 dimensões), em um notebook sem GPU dedicada (i5 de 11ª geração, 7,7 GB de RAM), medi 0,4 trecho/s em um teste curto e 0,2 trecho/s na indexação completa. Para 1.458 trechos de cerca de 1.600 caracteres, isso dá de 1 a 2 horas, uma vez só: o cache em disco guarda cada lote e a indexação retoma de onde parou. Lotes de 4 deram o melhor rendimento; lotes de 32 estouravam o limite de tempo.
- **O FAISS não abre caminhos com acento no Windows.** O índice é gravado e lido como bytes pelo Python, para o repositório poder ser clonado em qualquer pasta (por exemplo, uma com acento no nome, como `C:/Users/João/`). Achado por um teste, cuja pasta temporária tinha "ó" no nome.
- **Um teste dependia do ambiente.** A `OPENAI_API_KEY` do Windows fazia passar um teste que devia falhar por falta de chave. Os testes agora declaram o que esperam, sem depender de quem os roda.
- **Um índice por base.** Em um índice único, 60 contratos tiram o artigo da lei dos 3 primeiros resultados; pesquisando por base e juntando depois, ele aparece. O teste reproduz isso em miniatura. Minha previsão inicial estava errada: eu achava que a garantia de representação era o que salvava a base pequena, mas já a busca por base resolve o essencial, e a garantia cobre o caso de uma base com notas baixas.
- **Trecho sem data de publicação é mantido** quando se filtra por `vigente_em`: sem data, não dá para afirmar que saiu de vigor. É uma escolha que favorece não esconder evidência.
- **A consulta ao FTS5 é montada pelo código.** Cada palavra vai entre aspas e as palavras são unidas por OR, então um usuário não consegue injetar operadores da busca (`AND`, `NEAR`, `*`, parênteses).

Observações do passo 1.3 (cada uma só apareceu ao olhar o resultado real):

- **Ordem de leitura do PDF.** Nos decretos salvos pelo navegador, o rótulo "Art. 5º" fica em um bloco separado do texto do artigo, e a leitura padrão os entrega embaralhados (no decreto 48.820, só 4 de 20 artigos eram reconhecidos). A leitura ordenada por posição na página (`sort=True`) resolve: agora todos os artigos de todos os decretos são detectados (38/38, 53/53, 66/66, 37/37, 20/20, 38/38 e 18/18).
- **Quebra de linha no HTML da Lei.** O Planalto escreve "Art." e "75." separados por uma quebra no código-fonte, que em HTML vale um espaço. Tratada como linha nova, ela escondia 73 artigos, inclusive o Art. 75 (dispensa). Hoje os 194 artigos estão presentes.
- **CPF só com dígito verificador.** A regra "11 dígitos é CPF" apagava 50 códigos de produto de uma tabela de edital. Agora um CPF sem pontuação só é mascarado se o cálculo do módulo 11 fechar; com a pontuação típica (123.456.789-09), sempre. Trechos afetados caíram de 65 para 16.
- **E-mail funcional de órgão público fica visível.** Domínios `.gov.br`, `.leg.br`, `.jus.br`, `.mp.br` e `.mil.br` são contato institucional público. Os demais são mascarados.
- **Cabeçalho de seção.** Um cabeçalho como "7. DA PARTICIPAÇÃO" seguido de texto comum era colado ao texto e a seção passava despercebida. E "Seção I" com inicial maiúscula não abria um nível da hierarquia.
- **Limites.** Tabelas dos editais são lidas como texto linear (sem estrutura de linhas e colunas). Um decreto que altera outro cita, entre aspas, artigos do decreto alterado, que viram trechos extras (com id `-r2`). Os cabeçalhos "TÍTULO", "CAPÍTULO" e "Seção" vêm juntos com o título na mesma linha.

Observação do passo 1.1: o mascaramento de telefone só age com um sinal claro (DDD entre parênteses, celular com 9 na frente ou uma palavra como "tel" antes). Um número "NNNN-NNNN" sozinho é ambíguo em contratações ("2025-2026", "Edital 1234-5678"), e mascará-lo estragaria os textos.

## Fase 2: cenário de pesquisa acadêmica (em andamento)

- [x] **2.0 Generalização do motor.** A tabela de capacidades (entidades, campos, o que aceita filtro, soma ou agrupamento), as regras de recusa próprias do cenário e os textos dos prompts saíram do código e foram para `scenarios/<id>/capacidades.yaml`, lidos por `src/capacidades.py`. O arquivo é validado ao carregar (tipo de campo, nomes de tabela e de coluna, expressões calculadas, regex). O YAML do RJ foi gerado a partir das definições antigas e conferido campo a campo antes de remover o código; os 281 testes do RJ continuaram passando. Um teste com os dois cenários prova que um plano de um não vale no outro e que a regra de recusa de um não vaza para o outro.
- [x] **2.1 Coleta e normalização do OpenAlex.** Os 1.000 artigos mais citados de cada ano (2020 a 2024) de 10 instituições do RJ, com resumo: **5.000 trabalhos, 25 páginas, 160 MB, 0 falhas**. Normalizados em três tabelas: `trabalho` (5.000), `producao_instituicao` (64.294 pares trabalho–instituição) e `financiamento` (42.394 pares trabalho–financiador). 3.740 trabalhos declaram financiador; todos têm resumo; 4.896 têm ao menos uma das 10 instituições-alvo. O coletor ganhou `campo_itens`, `max_paginas` e `{chave}` dentro de parâmetros (com chaves `_` que não vão para a URL), com testes.
- [ ] 2.2 Editais de fomento e normas de ética (base normativa do cenário): escolher e fixar as URLs, ingerir e indexar.
- [ ] 2.3 Rodada real: as 10 perguntas de avaliação do cenário (PE-01 a PE-10) com um modelo de verdade.

Lições da fase 2:

- **O segundo cenário achou um erro que o primeiro escondia.** O desempate da listagem no roteador usava a coluna `numero_controle_pncp`, que só existe no RJ. O teste de pesquisa falhou na primeira execução. Agora o desempate usa as colunas de `exibir`, que cada cenário declara.
- **Sondar o formato antes da coleta cheia, e não só a contagem.** A primeira coleta saiu com todas as instituições vazias: o campo `institutions` do OpenAlex vem sempre vazio, e as instituições estão em `authorships`. Foram duas coletas jogadas fora (uma de teste e uma com o campo errado), as duas apagadas antes de publicar. A coleta de verdade passou a custar 160 MB em vez de 45 MB, por causa dos artigos de colaboração gigante.
- **Nomes oficiais, não siglas.** A FAPERJ aparece como "Fundação Carlos Chagas Filho de Amparo à Pesquisa do Estado do Rio de Janeiro". Um filtro por "FAPERJ" não acharia nada; a tabela de capacidades ensina o analisador a usar o nome por extenso.
- **"Instituições do RJ" precisa de um marcador.** Sem ele, a produção por instituição mistura as 10 do RJ com coautoras como USP e CNRS. A coluna `instituicao_alvo` é preenchida lendo os identificadores do filtro do próprio manifesto, então a seleção e a análise não podem divergir.
- **Limites da amostra:** são os mais citados, não a produção toda; os mais citados incluem colaborações gigantes; a ordenação da API não aceita desempate, então trabalhos com a mesma contagem no corte podem trocar entre coletas (o inventário guarda o SHA-256 das páginas coletadas).

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
