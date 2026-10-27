# Cenário: saúde pública e protocolos clínicos

**Estado:** planejado (fase 3, a mais pesada em extração).

## O problema

O Ministério da Saúde publica os PCDT (Protocolos Clínicos e Diretrizes Terapêuticas), que definem como diagnosticar e tratar cada doença no SUS. Eles mudam com o tempo, e versões antigas e vigentes circulam em PDFs parecidos. Quem consulta a versão errada recebe uma conduta que foi substituída, e não uma resposta "mais ou menos certa".

## As quatro bases

| Base | Conteúdo | Fonte | Acesso |
|---|---|---|---|
| Normativa | 15 a 20 PCDT, incluindo pares versão antiga + vigente | Conitec / Ministério da Saúde | PDF por URL |
| Estruturada | RENAME e bulário | Ministério da Saúde, ANVISA | Tabular e PDF |
| Agregada | Indicadores de dispensação, cobertura e internações | DATASUS, dados abertos | CSV |
| Conversacional | Registros de atendimento e ouvidoria | Sintética | Texto |

## O ponto central: a data de vigência

Se a data de vigência não estiver no metadado de cada chunk, o sistema mistura duas versões do mesmo protocolo e responde com a média das duas, com alta confiança, porque os trechos são quase idênticos. É o campo `campo_vigencia` do manifesto, e é obrigatório.

## O ponto de segurança: nunca prescrever

O sistema explica o conteúdo do protocolo, mas **não** indica dose, troca ou suspensão de medicamento para uma pessoa. A recusa é da prescrição individual, e não do conteúdo. Ver [sensibilidade.yaml](sensibilidade.yaml).

## Arquivos do cenário

| Arquivo | Para quê |
|---|---|
| [corpus_manifest.yaml](corpus_manifest.yaml) | O que entra na base, de onde e como |
| [PERSONAS.md](PERSONAS.md) | Três personas e as perguntas de cada uma |
| [sensibilidade.yaml](sensibilidade.yaml) | O que responder, mascarar e recusar |
| [eval/questions.json](eval/questions.json) | 10 perguntas com rota e fonte esperadas (uma é uma falha prevista) |

## Limitações já conhecidas

- Não há download em lote: os PDFs são coletados um a um, e vários são escaneados.
- Nenhum dado foi coletado ainda; as doenças e os medicamentos das perguntas são marcadores.
- Nada neste repositório substitui orientação de um profissional de saúde.
