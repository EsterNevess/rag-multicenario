"""Normalização da saúde: tabelas da RENAME em PDF e catálogo de versões de PCDT."""

from __future__ import annotations

import sqlite3

import pymupdf
import pytest

from src.fetch_corpus import Pastas
from src.normalize_saude import (
    componentes_do_nome,
    extrair_medicamentos,
    limpar,
    linhas_de_pcdt,
    marcar_constar_na_rename,
    medicamentos_do_pcdt,
    normalizar,
    papel_da_coluna,
)

CABECALHO = ["Denominação Comum (DCB)", "Concentração", "Forma Farmacêutica", "Componente de Financiamento", "Código ATC"]
COLUNAS_X = [40, 190, 280, 390, 500, 570]


def _grade(pagina, linhas, topo=100, altura=22, sem_divisoria_na_coluna_0=()):
    """Desenha uma tabela com grade; `sem_divisoria_na_coluna_0` lista as linhas cujo nome continua na célula de cima."""
    for i, textos in enumerate(linhas):
        y0 = topo + i * altura
        for j, texto in enumerate(textos):
            if j == 0 and i in sem_divisoria_na_coluna_0:
                continue
            ate = y0 + altura
            if j == 0:  # a célula do nome se estende pelas linhas seguintes sem texto
                k = i + 1
                while k in sem_divisoria_na_coluna_0:
                    ate += altura
                    k += 1
            pagina.draw_rect(pymupdf.Rect(COLUNAS_X[j], y0, COLUNAS_X[j + 1], ate), color=(0, 0, 0), width=0.8)
            if texto:
                pagina.insert_text((COLUNAS_X[j] + 3, y0 + 14), texto, fontsize=7)


def _pdf(tmp_path, paginas):
    doc = pymupdf.open()
    for desenhar in paginas:
        desenhar(doc.new_page(width=700, height=500))
    caminho = tmp_path / "rename.pdf"
    doc.save(caminho)
    doc.close()
    return caminho


def _pagina_a(titulo, linhas, **kw):
    def desenhar(pagina):
        pagina.insert_text((40, 60), titulo, fontsize=11)
        _grade(pagina, [CABECALHO, *linhas], **kw)

    return desenhar


def test_papel_da_coluna_reconhece_cabecalhos_com_palavras_coladas_e_com_erro_de_grafia_do_pdf():
    assert papel_da_coluna("DenominaçãoComum\nBrasileira(DCB)") == "dcb"
    assert papel_da_coluna("Concentração/\nComposição") == "concentracao"
    assert papel_da_coluna("FormaFarmacêutica​") == "forma"
    assert papel_da_coluna("Componentede i fnanciamento daAssistência Farmacêutica") == "componente"
    assert papel_da_coluna("Código\nATC") == "atc"
    assert papel_da_coluna("CódigoATC") == "atc"
    assert papel_da_coluna("i\nClassifcação\nAWaRe") == "aware"
    assert papel_da_coluna("i Classifcação ATC") is None  # coluna de classificação, não de código
    assert papel_da_coluna("Grupode Financiamento") == "componente"


def test_limpar_junta_linhas_tira_nota_de_rodape_e_trata_traco_como_vazio():
    assert limpar("comprimido de liberação \nprolongada") == "comprimido de liberação prolongada"
    assert limpar("Procedimento Hospitalar(1)") == "Procedimento Hospitalar"
    assert limpar("-") is None
    assert limpar(" ​ ") is None
    assert limpar(None) is None


def test_extrai_linhas_da_tabela_com_o_grupo_atc_da_pagina_e_repete_o_nome_das_celulas_mescladas(tmp_path):
    caminho = _pdf(tmp_path, [
        _pagina_a("A: Aparelho digestivo e metabolismo", [
            ["insulina humana NPH", "100 UI/mL", "suspensão injetável", "Básico", "A10AC01"],
            ["mesalazina", "400 mg", "comprimido", "Especializado", "A07EC02"],
            [None, "500 mg", "supositório retal", "Especializado", "A07EC02"],
        ], sem_divisoria_na_coluna_0=(3,)),
    ])
    linhas, relatorio = extrair_medicamentos(caminho, ano_edicao=2024)
    assert [(l["dcb"], l["concentracao"], l["forma"]) for l in linhas] == [
        ("insulina humana NPH", "100 UI/mL", "suspensão injetável"),
        ("mesalazina", "400 mg", "comprimido"),
        ("mesalazina", "500 mg", "supositório retal"),
    ]
    assert {l["grupo_atc"] for l in linhas} == {"A"}
    assert linhas[0]["grupo_atc_nome"] == "Aparelho digestivo e metabolismo"
    assert linhas[0]["componente"] == "Básico" and linhas[0]["atc"] == "A10AC01"
    assert linhas[0]["pagina"] == 1 and linhas[0]["ano_edicao"] == 2024
    assert [l["id"] for l in linhas] == [1, 2, 3]
    assert relatorio["linhas_sem_codigo_atc"] == 0


def test_tabela_que_continua_na_pagina_seguinte_sem_cabecalho_e_lida_com_o_mesmo_mapeamento(tmp_path):
    def continuacao(pagina):
        pagina.insert_text((40, 60), "continua", fontsize=11)
        _grade(pagina, [["omeprazol", "20 mg", "cápsula", "Básico", "A02BC01"], ["pantoprazol", "40 mg", "comprimido", "Básico", "A02BC02"]])

    caminho = _pdf(tmp_path, [
        _pagina_a("A: Aparelho digestivo e metabolismo", [["lactulose", "667 mg/mL", "xarope", "Básico", "A06AD11"]]),
        continuacao,
    ])
    linhas, _ = extrair_medicamentos(caminho)
    assert [l["dcb"] for l in linhas] == ["lactulose", "omeprazol", "pantoprazol"]
    assert linhas[1]["pagina"] == 2 and linhas[1]["grupo_atc"] == "A"  # o grupo continua o da página anterior


def test_tabela_sem_codigo_atc_nao_e_lida(tmp_path):
    def outra(pagina):
        pagina.insert_text((40, 60), "ANEXO I", fontsize=11)
        _grade(pagina, [["Denominação Comum (DCB)", "Relatório", "Portaria", "Grupo de Financiamento", "Documento"], ["abatacepte", "r1", "p1", "Especializado", "d1"]])

    caminho = _pdf(tmp_path, [outra])
    linhas, relatorio = extrair_medicamentos(caminho)
    assert linhas == [] and relatorio["paginas_com_tabela"] == 0


def test_para_no_apendice_b_para_nao_repetir_os_medicamentos_agrupados_por_anexo(tmp_path):
    def apendice_b(pagina):
        pagina.insert_text((40, 60), "APÊNDICE B", fontsize=11)
        pagina.insert_text((40, 80), "RENAME POR ANEXOS", fontsize=11)
        _grade(pagina, [CABECALHO, ["insulina humana NPH", "100 UI/mL", "suspensão injetável", "Básico", "A10AC01"]], topo=120)

    caminho = _pdf(tmp_path, [
        _pagina_a("A: Aparelho digestivo e metabolismo", [["insulina humana NPH", "100 UI/mL", "suspensão injetável", "Básico", "A10AC01"]]),
        apendice_b,
    ])
    linhas, _ = extrair_medicamentos(caminho)
    assert len(linhas) == 1


def test_linhas_de_pcdt_marca_como_substituida_a_versao_com_data_de_fim():
    manifesto = {"bases": [
        {"base_id": "rename_estruturado"},
        {"base_id": "pcdt_normativo", "acesso": {"urls": [
            {"doenca": "Artrite reumatoide", "versao": "PC 15/2017", "rotulo": "r1", "data_publicacao": "2018-01-04", "data_vigencia_fim": "2019-11-04"},
            {"doenca": "Artrite reumatoide", "versao": "PC 33/2026", "rotulo": "r2", "data_publicacao": "2026-01-19"},
        ]}},
    ]}
    versoes = linhas_de_pcdt(manifesto)
    assert [(v["id"], v["situacao"], v["data_vigencia_fim"]) for v in versoes] == [(1, "substituída", "2019-11-04"), (2, "vigente", None)]


def test_normalizar_grava_as_duas_tabelas_e_os_metadados(tmp_path):
    cenario = tmp_path / "scenarios" / "saude"
    cenario.mkdir(parents=True)
    manifesto = cenario / "corpus_manifest.yaml"
    manifesto.write_text(
        "scenario_id: saude\nversion: '1'\nstatus: x\ndescription: x\nbases:\n"
        "  - base_id: pcdt_normativo\n    natureza: normativa\n    fonte: f\n    acesso:\n      tipo: http_pdf\n      urls:\n"
        "        - {url: 'http://x/a.pdf', rotulo: r, doc_type: pcdt, doenca: Parkinson, versao: PC 10/2017, data_publicacao: '2017-11-09'}\n"
        "    selecao_deterministica: true\n    volume_alvo: 1\n    formato: pdf\n    licenca: l\n    sensitivity_default: publico\n    campo_vigencia: data_publicacao\n"
        "  - base_id: rename_estruturado\n    natureza: estruturada\n    fonte: f\n    acesso: {tipo: http_pdf, urls: [{url: 'http://x/r.pdf', rotulo: r, doc_type: rename}]}\n"
        "    selecao_deterministica: true\n    volume_alvo: 1\n    formato: pdf\n    licenca: l\n    sensitivity_default: publico\n    campo_vigencia: ano_edicao\n",
        encoding="utf-8",
    )
    pasta = tmp_path / "data" / "raw" / "saude" / "v1" / "rename_estruturado"
    pasta.mkdir(parents=True)
    origem = _pdf(tmp_path, [_pagina_a("A: Aparelho digestivo e metabolismo", [["lactulose", "667 mg/mL", "xarope", "Básico", "A06AD11"]])])
    (pasta / "rename.pdf").write_bytes(origem.read_bytes())

    pastas = Pastas(cenario_id="saude", cenario=cenario, dados=tmp_path / "data")
    relatorio = normalizar(manifesto, "v1", pastas)

    assert relatorio["tabelas"] == {"medicamento": 1, "pcdt_versao": 1, "pcdt_medicamento": 0}
    conexao = sqlite3.connect(tmp_path / "data" / "normalized" / "saude" / "v1.sqlite")
    assert conexao.execute("SELECT dcb, componente, atc FROM medicamento").fetchall() == [("lactulose", "Básico", "A06AD11")]
    assert conexao.execute("SELECT valor FROM metadados WHERE chave='cenario'").fetchone() == ("saude",)
    conexao.close()


def test_normalizar_recusa_versao_sem_pdf_da_rename(tmp_path):
    cenario = tmp_path / "scenarios" / "saude"
    cenario.mkdir(parents=True)
    (tmp_path / "data" / "raw" / "saude" / "v1").mkdir(parents=True)
    manifesto = cenario / "corpus_manifest.yaml"
    manifesto.write_text(
        "scenario_id: saude\nversion: '1'\nstatus: x\ndescription: x\nbases:\n"
        "  - base_id: rename_estruturado\n    natureza: estruturada\n    fonte: f\n    acesso: {tipo: http_pdf, urls: [{url: 'http://x/r.pdf', rotulo: r, doc_type: rename}]}\n"
        "    selecao_deterministica: true\n    volume_alvo: 1\n    formato: pdf\n    licenca: l\n    sensitivity_default: publico\n    campo_vigencia: ano_edicao\n",
        encoding="utf-8",
    )
    pastas = Pastas(cenario_id="saude", cenario=cenario, dados=tmp_path / "data")
    with pytest.raises(FileNotFoundError):
        normalizar(manifesto, "v1", pastas)


def _trecho(texto, versao="PC 10/2017", doenca="Parkinson", parte=None, fim=None):
    return {"metadata": {"versao": versao, "doenca": doenca, "data_publicacao": "2017-11-09", "data_vigencia_fim": fim, "parte": parte}, "texto": "titulo - Item 6.3\n" + texto}


def test_componentes_do_nome_ignora_sal_parenteses_acento_e_separador():
    assert componentes_do_nome("Levodopa/carbidopa") == componentes_do_nome("levodopa + carbidopa")
    assert componentes_do_nome("Cloridrato de amantadina") == componentes_do_nome("amantadina")
    assert componentes_do_nome("Metilprednisolona (acetato) (intra-articular)") == frozenset({"metilprednisolona"})
    assert componentes_do_nome("Fosfato sódico de prednisolona") == frozenset({"prednisolona"})
    assert componentes_do_nome("insulina humana NPH") != componentes_do_nome("insulina humana regular")


def test_medicamentos_do_pcdt_le_lista_em_linha_e_em_topicos_e_descarta_texto_corrido():
    em_linha = "6.3   FÁRMACOS    Pramipexol: comprimidos de 0,125 e 1 mg.    Amantadina: comprimidos de 100 mg.    Entacapona: comprimidos de 200 mg. "
    em_topicos = "8.4 FÁRMACOS Anti-inflamatórios (AINE)\n- Ibuprofeno: comprimidos de 200 mg.\n- Metotrexato: comprimidos de 2,5 mg; frascos de 50 mg/2 ml.\n- Observação: ver item 9."
    linhas, sem_secao = medicamentos_do_pcdt([_trecho(em_linha), _trecho(em_topicos, versao="PC 15/2017", doenca="Artrite")])
    assert [(l["versao"], l["medicamento"]) for l in linhas] == [
        ("PC 10/2017", "Pramipexol"), ("PC 10/2017", "Amantadina"), ("PC 10/2017", "Entacapona"),
        ("PC 15/2017", "Ibuprofeno"), ("PC 15/2017", "Metotrexato"),
    ]  # "Observação: ver item 9." não tem forma farmacêutica nem dose
    assert linhas[0]["apresentacao"] == "comprimidos de 0,125 e 1 mg"
    assert sem_secao == []


def test_medicamentos_do_pcdt_so_le_o_primeiro_trecho_da_secao_e_aponta_versao_sem_secao():
    primeiro = "8. FÁRMACOS E ESQUEMA DE ADMINISTRAÇÃO\n- Ibuprofeno: comprimidos de 200 mg."
    continuacao = "- Metotrexato: dose de 7,5 mg por semana, por via oral."
    sem_lista = "7 TRATAMENTO O tratamento inclui educação e insulinoterapia."
    linhas, sem_secao = medicamentos_do_pcdt([
        _trecho(primeiro, versao="A", parte=1), _trecho(continuacao, versao="A", parte=2), _trecho(sem_lista, versao="B", doenca="Diabetes"),
    ])
    assert [l["medicamento"] for l in linhas] == ["Ibuprofeno"]
    assert sem_secao == ["B"]


def test_medicamentos_do_pcdt_marca_versao_substituida():
    linhas, _ = medicamentos_do_pcdt([_trecho("6.3 FÁRMACOS    Pramipexol: comprimidos de 1 mg.", fim="2020-01-01")])
    assert linhas[0]["situacao"] == "substituída"


def test_marcar_constar_na_rename_compara_principio_ativo_e_registra_o_nome_da_rename():
    linhas = [{"medicamento": "Pramipexol"}, {"medicamento": "Levodopa/carbidopa"}, {"medicamento": "Rasagilina"}]
    marcar_constar_na_rename(linhas, ["dicloridrato de pramipexol", "levodopa + carbidopa", "levodopa + benserazida"])
    assert [(l["na_rename"], l["dcb_na_rename"]) for l in linhas] == [
        ("sim", "dicloridrato de pramipexol"), ("sim", "levodopa + carbidopa"), ("não", None),
    ]
    assert [l["id"] for l in linhas] == [1, 2, 3]


def test_normalizar_grava_pcdt_medicamento_a_partir_dos_trechos_ingeridos(tmp_path):
    import json

    cenario = tmp_path / "scenarios" / "saude"
    cenario.mkdir(parents=True)
    manifesto = cenario / "corpus_manifest.yaml"
    manifesto.write_text(
        "scenario_id: saude\nversion: '1'\nstatus: x\ndescription: x\nbases:\n"
        "  - base_id: rename_estruturado\n    natureza: estruturada\n    fonte: f\n    acesso: {tipo: http_pdf, urls: [{url: 'http://x/r.pdf', rotulo: r, doc_type: rename}]}\n"
        "    selecao_deterministica: true\n    volume_alvo: 1\n    formato: pdf\n    licenca: l\n    sensitivity_default: publico\n    campo_vigencia: ano_edicao\n",
        encoding="utf-8",
    )
    pasta = tmp_path / "data" / "raw" / "saude" / "v1" / "rename_estruturado"
    pasta.mkdir(parents=True)
    origem = _pdf(tmp_path, [_pagina_a("A: Aparelho digestivo e metabolismo", [["cloridrato de amantadina", "100 mg", "comprimido", "Básico", "N04BB01"]])])
    (pasta / "rename.pdf").write_bytes(origem.read_bytes())
    processed = tmp_path / "data" / "processed" / "saude"
    processed.mkdir(parents=True)
    trecho = _trecho("6.3 FÁRMACOS    Amantadina: comprimidos de 100 mg.    Rasagilina: comprimidos de 1 mg.")
    (processed / "v1_pcdt_normativo.jsonl").write_text(json.dumps(trecho, ensure_ascii=False) + "\n", encoding="utf-8")

    relatorio = normalizar(manifesto, "v1", Pastas(cenario_id="saude", cenario=cenario, dados=tmp_path / "data"))

    assert relatorio["tabelas"]["pcdt_medicamento"] == 2
    conexao = sqlite3.connect(tmp_path / "data" / "normalized" / "saude" / "v1.sqlite")
    assert conexao.execute("SELECT medicamento, na_rename FROM pcdt_medicamento ORDER BY id").fetchall() == [("Amantadina", "sim"), ("Rasagilina", "não")]
    conexao.close()
