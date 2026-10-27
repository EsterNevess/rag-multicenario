# Personas: contratações públicas do RJ

Três personas próprias. As perguntas de cada uma estão em [eval/questions.json](eval/questions.json), classificadas por rota esperada e por tipo.

## Teresa, pregoeira de uma prefeitura da Baixada Fluminense

**Situação:** conduz pregões e precisa montar o termo de referência de uma compra grande de material de escritório antes do fim do mês. Tem pressa e quer ter certeza de que está citando a regra certa.

**O que precisa:** regra legal em vigor (modalidades, limites de dispensa) e referência do que outros órgãos do Estado já publicaram para o mesmo objeto.

| ID | Pergunta | Rota esperada | Tipo |
|---|---|---|---|
| RJ-01 | Quais são as modalidades de licitação previstas na Lei 14.133/2021? | normativa | factual |
| RJ-02 | Qual é o valor limite atual para dispensa de licitação por valor em compras e serviços, e qual decreto estadual regulamenta a contratação direta no RJ? | normativa | vigência |
| RJ-03 | Quais contratações de material de escritório foram publicadas por órgãos do RJ em 2026? | estruturada | filtro |

## Diego, auditor de controle interno de uma secretaria estadual

**Situação:** prepara um relatório trimestral sobre contratações diretas e contratos de alto valor. Precisa de números confiáveis e com recorte explícito, porque o relatório vai para o tribunal de contas.

**O que precisa:** contagens e filtros por valor e modalidade, com o período sempre declarado. E sabe que o sistema não pode lhe dar um parecer de fraude.

| ID | Pergunta | Rota esperada | Tipo |
|---|---|---|---|
| RJ-04 | Quantas contratações diretas (dispensa e inexigibilidade) houve em 2026 e qual a proporção sobre o total? | agregada + estruturada | filtro |
| RJ-05 | Quais contratos acima de R$ 1 milhão foram assinados por órgãos estaduais em 2026? | estruturada | filtro |
| RJ-06 | Esse contrato da Secretaria X foi fraude? | nenhuma (guardrail) | recusa |

## Lúcia, dona de uma microempresa de limpeza

**Situação:** nunca vendeu para o governo e não tem vocabulário técnico. Quer saber se pode participar e se há oportunidades abertas.

**O que precisa:** uma resposta em linguagem simples que junte a regra (tratamento diferenciado para microempresas, documentos de habilitação) com o que está aberto agora.

| ID | Pergunta | Rota esperada | Tipo |
|---|---|---|---|
| RJ-07 | Sou microempresa: tenho tratamento diferenciado em licitações e há contratações abertas de serviços de limpeza? | normativa + estruturada | cross-source |
| RJ-08 | Quais documentos preciso apresentar para me habilitar em um pregão? | normativa | factual |
| RJ-09 | Qual a previsão do tempo no Rio amanhã? | nenhuma (guardrail) | fora de escopo |

## A pergunta que acredito que o sistema vai errar

| ID | Pergunta | Rota esperada | Tipo |
|---|---|---|---|
| RJ-10 | Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2026? | agregada + estruturada | filtro |

**Hipótese de falha:** o preço por quilo exige itens de contratação e normalização de unidades (kg, saco de 5 kg, fardo, "unidade"). Sem os itens ou com unidades sem equivalência de massa, a média fica vazia ou errada, mesmo com resposta fluente. No projeto em equipe, uma pergunta idêntica já voltou sem resultados. A fase de avaliação vai medir se acontece de novo e por quê.

## Perguntas que o roteador não pode errar

Mesmo vocabulário, rotas diferentes:

- "Quais as modalidades de licitação?" pede a **norma** (normativa).
- "Quantas contratações por modalidade em 2026?" pede **dados** (estruturada).
- Roteamento por palavra-chave levaria as duas à Lei 14.133 e devolveria uma definição no lugar de um número.
