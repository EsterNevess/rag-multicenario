# Inventário do corpus: saude (v2)

Coletado em: 2026-11-16T14:58:16-03:00  
Hash do manifesto: `sha256:7b3bb7f0f45ac9fd0a1a0940f9a6b016a41fc08bd0314c4fd2caed594b8c9570`

**Total:** 9 arquivos, 19.6 MB, 0 falha(s).

## Por base

| Base | Natureza | Estado | Arquivos | Formatos | Tamanho | Alvo | Divergência |
|---|---|---|---|---|---|---|---|
| pcdt_normativo | normativa | coletada | 8 | pdf: 8 | 18.2 MB | 8 | 0 |
| rename_estruturado | estruturada | coletada | 1 | pdf: 1 | 1.4 MB | 1 | 0 |
| indicadores_agregado | agregada | nao_coletada | 0 | - | 0 B | a definir | - |
| ouvidoria_conversacional | conversacional | nao_coletada | 0 | - | 0 B | 30 | - |

## Bases ainda não coletadas

- `indicadores_agregado`: decisão: os dados de internação e dispensação do SUS vêm em arquivos .dbc do FTP do DATASUS (sem CSV simples), e a agregada é calculada em SQL sobre o que já se coleta (RENAME e catálogo de PCDT)
- `ouvidoria_conversacional`: gerada por `python -m src.synth build` (semente fixa), fora da coleta versionada; a ingestão lê data/synthetic

## Falhas

Nenhuma.

## Arquivos

| Arquivo | Base | Tamanho | SHA-256 | Origem |
|---|---|---|---|---|
| antigo-conitec.saude.gov.br_images_Relatorios_2020_Relatorio_PCDT_ArtriteReumatoide_460_2020.pdf | pcdt_normativo | 3.0 MB | `35a9ae139d0c` | http://antigo-conitec.saude.gov.br/images/Relatorios/2020/Relatorio_PCDT_ArtriteReumatoide_460_2020.pdf |
| www.gov.br_conitec_pt-br_midias_consultas_relatorios_2020_20200908_relatorio_artrite_reumatoide_551.pdf__display-file_file.pdf | pcdt_normativo | 3.7 MB | `ca0b29af851e` | https://www.gov.br/conitec/pt-br/midias/consultas/relatorios/2020/20200908_relatorio_artrite_reumatoide_551.pdf/@@display-file/file |
| www.gov.br_conitec_pt-br_midias_protocolos_20211112_portaria_conjunta_16_pcdt_ar.pdf__display-file_file.pdf | pcdt_normativo | 3.8 MB | `cf0edbd769ce` | https://www.gov.br/conitec/pt-br/midias/protocolos/20211112_portaria_conjunta_16_pcdt_ar.pdf/@@display-file/file |
| www.gov.br_conitec_pt-br_midias_protocolos_2026_pcdt-diabete-melito-tipo-2__display-file_file.pdf | pcdt_normativo | 1.7 MB | `f90b782997a5` | https://www.gov.br/conitec/pt-br/midias/protocolos/2026/pcdt-diabete-melito-tipo-2/@@display-file/file |
| www.gov.br_conitec_pt-br_midias_protocolos_pcdt_doenca_de_parkinson_2017-1.pdf__display-file_file.pdf | pcdt_normativo | 496.5 KB | `a913be7e3bb9` | https://www.gov.br/conitec/pt-br/midias/protocolos/pcdt_doenca_de_parkinson_2017-1.pdf/@@display-file/file |
| www.gov.br_conitec_pt-br_midias_protocolos_publicacoes_ms_pcdt_diabetesmellitus_tipoi_isbn_19-08-2020.pdf__display-file_file.pdf | pcdt_normativo | 1.0 MB | `bb74ce8a3519` | https://www.gov.br/conitec/pt-br/midias/protocolos/publicacoes_ms/pcdt_diabetesmellitus_tipoi_isbn_19-08-2020.pdf/@@display-file/file |
| www.gov.br_conitec_pt-br_midias_relatorios_2017_pcdt_ar_final.pdf__display-file_file.pdf | pcdt_normativo | 1.4 MB | `7fc783af78d3` | https://www.gov.br/conitec/pt-br/midias/relatorios/2017/pcdt_ar_final.pdf/@@display-file/file |
| www.gov.br_saude_pt-br_assuntos_pcdt_a_artrite-reumatoide__download_file.pdf | pcdt_normativo | 3.0 MB | `857824309b1a` | https://www.gov.br/saude/pt-br/assuntos/pcdt/a/artrite-reumatoide/@@download/file |
| www.gov.br_saude_pt-br_composicao_sectics_rename_publicacoes_relacao-nacional-de-medicamentos-essenciais-2024.pdf | rename_estruturado | 1.4 MB | `a991dd093477` | https://www.gov.br/saude/pt-br/composicao/sectics/rename/publicacoes/relacao-nacional-de-medicamentos-essenciais-2024 |
