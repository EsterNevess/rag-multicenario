# Segurança: o que foi verificado

Verificações feitas em 17/11/2026 (a varredura do histórico foi repetida em 23/11/2026, abaixo), o que cada uma achou e o que **não** cobre. Não é uma auditoria de segurança nem um teste de invasão.

## Segredos e dados pessoais no repositório

| Verificação | Resultado |
|---|---|
| Arquivos rastreados pelo Git (`.env`, bancos, índices, PDFs, logs, pesos de modelos) | Nenhum. O `.gitignore` cobre `.env`, `data/` (exceto os inventários), `logs/` e `data/models/`. |
| Varredura por expressões regulares no histórico inteiro (chaves `gsk_`, `sk-`, `ghp_`, `AKIA`, chaves privadas, atribuições de senha e token, e-mails, CPF, telefone) | Nenhum segredo real. Só chaves obviamente falsas de teste (`gsk-teste`, `chave-de-teste`) e dados pessoais fictícios dos dados sintéticos. |
| `detect-secrets` 1.5.0 nos 131 arquivos rastreados | 656 alertas, todos falsos: 654 são SHA-256 de arquivos (nos inventários e no manifesto do RJ) e 2 são as chaves falsas de teste. |
| `detect-secrets` no histórico inteiro (diff de todos os commits) | 569 alertas, **todos** de linhas com um SHA-256 de 64 hexadecimais. Nenhuma chave, token, senha ou chave privada. |
| Chave da Groq | Existe só no `.env` local, que o Git ignora. Nunca foi impressa nem commitada. |

### Varredura do histórico repetida em 23/11/2026

Refeita sobre o histórico inteiro de então (**110 commits**, 30.032 linhas adicionadas, 145 arquivos distintos já versionados), antes de o repositório ser tornado público.

| Verificação | Resultado |
|---|---|
| Expressões regulares sobre as linhas adicionadas de todos os commits, em lotes de 10 commits (chaves `gsk_`, `sk-`, `ghp_`, `AKIA`, chaves privadas, `xox`, `AIza`, `hf_`, JWT, `Bearer`, atribuições de senha e token, CPF, telefone) | Nenhum segredo real. 50 acertos, todos de teste: `sk-segredo-de-teste`, `gsk-teste`, `chave-de-teste`, nomes de variável como `SecretStr`, senhas plantadas de propósito nos dados sintéticos e nos testes de redação (`Trocar@2026`, `Painel#8841`), e CPFs, telefones e e-mails fictícios. |
| **Controle:** 6 segredos falsos plantados num último lote (`gsk_`, `sk-`, `ghp_`, `AKIA`, chave privada, senha) | Os 6 foram achados. |
| Valor exato da chave da Groq do `.env` procurado em todo o histórico (sem ser impresso) | Não aparece em nenhum commit. |
| `detect-secrets` 1.5.0 sobre o diff de todos os commits (34.694 linhas, em 18 pedaços de 2.000 linhas) | 572 alertas: 569 são SHA-256 de 64 hexadecimais (os mesmos 569 de 17/11) e 3 são o controle. Nenhum outro. |
| **Controle do `detect-secrets`:** chave AWS, chave privada e token do GitHub falsos plantados no último pedaço | Os 3 foram achados. |
| `detect-secrets` sobre os 145 arquivos versionados hoje | 656 alertas, o mesmo número de 17/11: 654 SHA-256 (conferidos um a um, comparando o hash do alerta com os SHA-256 do próprio arquivo) e 2 chaves falsas de teste (`gsk-teste` em `tests/unit/test_generate.py`, `chave-de-teste` em `tests/unit/test_index.py`). |
| **Repetição de 24/11 com correção** (115 commits, 30.378 linhas adicionadas, 16 pedaços de 2.000 linhas, `PYTHONUTF8=1`, caminho relativo e controle plantado em **todos** os pedaços) | `detect-secrets`: 699 alertas = 633 SHA-256 de 64 hexadecimais (conferidos um a um pela linha) + 2 chaves falsas de teste (`gsk-teste` e `chave-de-teste`, em testes) + 64 do controle (4 alertas por pedaço, nos 16 pedaços). Nenhum outro. As expressões regulares sobre as mesmas linhas deram só as 2 chaves falsas de teste e 0 em chaves `gsk_`, `sk-`, `ghp_`, `AKIA`, chave privada, `xox` e `AIza`; o valor exato da chave do `.env` não aparece em nenhuma linha. |
| Arquivos já versionados com cara de segredo ou de dado local (`.env`, bancos, índices, logs, pesos de modelos) | Nenhum. |

- Os 5 CPFs distintos são números de exemplo conhecidos de validadores (`123.456.789-09`, `111.444.777-35`, `987.654.321-00` e duas sequências inválidas). Três passam no dígito verificador, o que é normal para esses exemplos; não dá para provar que nenhum pertence a uma pessoa real.
- Os e-mails de gmail, outlook e yahoo, com nomes fictícios, e os telefones estão em testes e nos arquivos de avaliação de redação (`redacao_holdout*.json`). `licitacao@saude.rj.gov.br` e `atendimento@faperj.br` aparecem só em `tests/unit/test_pii.py`.
- **Limites desta repetição:** o `detect-secrets` foi instalado depois da primeira passada do dia e só no ambiente virtual (não consta nos arquivos de dependências), então a varredura com ele foi feita à parte, em pedaços de 2.000 linhas porque um arquivo único grande o fez falhar em silêncio numa tentativa anterior; olhou o **conteúdo** dos commits e não os metadados de autoria (veja o e-mail da residência abaixo); binários e objetos inalcançáveis não são lidos como texto (o pacote do repositório tem uns 478 KiB). Vale o cuidado descrito logo abaixo: o resultado "limpo" só tem valor porque o controle foi achado.
- Se o histórico mudar até a publicação, a varredura precisa ser refeita.

**Falha silenciosa achada em 24/11, depois da varredura de 23/11.** No Windows, o `detect-secrets` pula o arquivo inteiro, sem erro e com 0 alertas, se ele tiver um caractere fora do ASCII que o Python não consiga ler com a codificação padrão (o caso foi `【` em uma linha de PROGRESSO.md), e também se o arquivo for dado por caminho absoluto. Na varredura de 23/11 o controle ficava só no último pedaço, então um pedaço anterior com esse tipo de caractere teria passado sem ser lido, sem eu perceber. A repetição de 24/11 acima corrige isso e vale no lugar da de 23/11 para o `detect-secrets`; as expressões regulares e a busca da chave exata, que não dependem dele, não foram afetadas. O número de SHA-256 subiu de 569 para 633, e não verifiquei quanto vem de commits novos (111 para 115) e quanto dos pedaços que antes podem ter sido pulados.

**Cuidado ao repetir a varredura do histórico.** O `detect-secrets` falhou em silêncio num arquivo único de 2 MB (0 alertas, mesmo com segredos plantados nele). A varredura só vale se o diff for dividido em pedaços pequenos (2.000 linhas) e se houver um **controle** em cada pedaço: plantam-se chaves falsas e confere-se que são achadas, com `PYTHONUTF8=1` e dentro da pasta do pedaço, com caminho relativo. Foi assim que se fez aqui.

```bash
git log -p --all -U0 --format='COMMIT %H' > historico.txt
# dividir historico.txt em pedaços de 2.000 linhas numa pasta com um repositório git vazio, e então:
detect-secrets scan --all-files .
```

## Dependências com vulnerabilidade conhecida

`pip-audit` 2.10.1 sobre os 67 pacotes instalados no ambiente do projeto (`--path .venv/Lib/site-packages`):

- **Antes:** 1 vulnerabilidade, no próprio `pip` 26.1.2 (PYSEC-2026-3721, corrigida no 26.2), que é a ferramenta de instalação e não uma dependência do projeto. Atualizado para o 26.3.
- **Depois:** "No known vulnerabilities found".
- Um workflow semanal (`.github/workflows/auditoria.yml`) repete essa verificação nas dependências fixadas, sem bloquear os pushes.

Neste computador o Python não verificava o certificado do `pypi.org`, porque a rede usa um certificado raiz instalado no Windows. A solução foi usar o repositório de certificados do sistema (`truststore.inject_into_ssl()`), sem desligar a verificação.

## O que não está coberto

- **O e-mail da residência** aparece como autor nos 80 primeiros commits (o histórico não foi reescrito, por decisão da autora). Os commits novos usam outro endereço.
- **Dados reais:** o projeto só usa dados públicos e sintéticos. A redação de dados pessoais perde mais da metade das frases sensíveis em texto novo (veja o [AVALIACAO.md](AVALIACAO.md)) e **não serve para dados reais**.
- **Envio de dados a terceiros:** com a Groq ou a OpenAI como provedor, as perguntas e os trechos de evidência saem do computador.
- **PyMuPDF é AGPL-3.0** (veja o [DADOS_E_LICENCAS.md](DADOS_E_LICENCAS.md)).
- **A aplicação em si:** não houve teste de invasão, de injeção de prompt em escala ou de carga. A guarda de entrada barra as tentativas de injeção que as regras conhecem, e isso foi testado só com exemplos escritos pela autora.
- Só se verifica o que as ferramentas conhecem: um segredo num formato que o `detect-secrets` e as expressões regulares não reconhecem passaria.
