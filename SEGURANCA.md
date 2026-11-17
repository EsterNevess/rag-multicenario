# Segurança: o que foi verificado

Verificações feitas em 17/11/2026, o que cada uma achou e o que **não** cobre. Não é uma auditoria de segurança nem um teste de invasão.

## Segredos e dados pessoais no repositório

| Verificação | Resultado |
|---|---|
| Arquivos rastreados pelo Git (`.env`, bancos, índices, PDFs, logs, pesos de modelos) | Nenhum. O `.gitignore` cobre `.env`, `data/` (exceto os inventários), `logs/` e `data/models/`. |
| Varredura por expressões regulares no histórico inteiro (chaves `gsk_`, `sk-`, `ghp_`, `AKIA`, chaves privadas, atribuições de senha e token, e-mails, CPF, telefone) | Nenhum segredo real. Só chaves obviamente falsas de teste (`gsk-teste`, `chave-de-teste`) e dados pessoais fictícios dos dados sintéticos. |
| `detect-secrets` 1.5.0 nos 131 arquivos rastreados | 656 alertas, todos falsos: 654 são SHA-256 de arquivos (nos inventários e no manifesto do RJ) e 2 são as chaves falsas de teste. |
| `detect-secrets` no histórico inteiro (diff de todos os commits) | 569 alertas, **todos** de linhas com um SHA-256 de 64 hexadecimais. Nenhuma chave, token, senha ou chave privada. |
| Chave da Groq | Existe só no `.env` local, que o Git ignora. Nunca foi impressa nem commitada. |

**Cuidado ao repetir a varredura do histórico.** O `detect-secrets` falhou em silêncio num arquivo único de 2 MB (0 alertas, mesmo com segredos plantados nele). A varredura só vale se o diff for dividido em pedaços pequenos (2.000 linhas) e se houver um **controle**: planta-se uma chave falsa no último pedaço e confere-se que ela é achada. Foi assim que se fez aqui.

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
