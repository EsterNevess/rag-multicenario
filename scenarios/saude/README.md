# Cenário: saúde pública e protocolos clínicos

**Estado:** construído (fase 3). Coleta, ingestão, índices, normalização, indicadores, ouvidoria sintética e regras de segurança prontos e testados; a rodada real com o modelo foi parcial (limite diário da Groq).

## O problema

O Ministério da Saúde publica os PCDT (Protocolos Clínicos e Diretrizes Terapêuticas), que definem como diagnosticar e tratar cada doença no SUS. Eles mudam com o tempo, e versões antigas e vigentes circulam em PDFs parecidos. Quem consulta a versão errada recebe uma conduta que foi substituída, e não uma resposta "mais ou menos certa".

## As quatro bases

| Base | Conteúdo | Fonte | Acesso |
|---|---|---|---|
| Normativa | 6 PCDT de 3 doenças (diabetes tipo 1, Parkinson e artrite reumatoide, esta com 4 versões de 2017 a 2026), 635 trechos | Conitec / Ministério da Saúde | PDF por URL |
| Estruturada | RENAME 2024: 996 linhas (medicamento + concentração + forma), 541 medicamentos distintos; e o catálogo das versões de PCDT | Ministério da Saúde | PDF de tabelas lido por célula |
| Agregada | 3 indicadores em SQL (apresentações por componente, por grupo ATC, versões de PCDT por doença) | Calculada sobre as bases acima | SQL |
| Conversacional | 30 registros de ouvidoria, 6 armadilhas | Sintética | Texto |

**Decisão sobre a agregada.** Dispensação, cobertura e internações vêm em arquivos `.dbc` do DATASUS, sem CSV simples. O projeto decidiu não coletá-los: a pergunta "quantas unidades foram dispensadas no estado?" (SA-05) é recusada, e o catálogo de indicadores diz o porquê.

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

- O corpus é um recorte datado: três doenças, e o PCDT de cada uma pode ter sido atualizado depois do documento coletado. A Portaria Conjunta 14/2020 da artrite reumatoide só existe como página HTML e não está no corpus, então entre 2020-09-08 e 2021-09-09 não há versão (o sistema não inventa uma).
- O PCDT vigente de diabetes tipo 2 não foi localizado em PDF oficial confirmado e ficou de fora.
- A RENAME é lida das tabelas do PDF. Um cruzamento com a contagem de códigos ATC do texto mostrou que as diferenças vêm de células com dois códigos; mesmo assim, não há garantia de cobrir toda forma de tabela. Medicamentos com dois códigos ficam com os dois na mesma célula.
- A redação de dado clínico na ouvidoria só pega o que as regras conhecem: 3 de 8 frases novas na primeira medição, 8 de 8 depois de generalizar (número otimista, as frases já eram conhecidas). A cobertura real é desconhecida.
- Comparar o que um PCDT cita com o que a RENAME lista (SA-04 e SA-10) exige ler o protocolo e a tabela; a falha prevista do SA-10 não foi medida.
- Nada neste repositório substitui orientação de um profissional de saúde.
