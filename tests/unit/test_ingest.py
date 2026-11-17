"""Testes da ingestão: extração de texto, corte em trechos, metadados e determinismo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest
import yaml

from src.fetch_corpus import Pastas, fetch
from src.ingest import (
    MAX_CHARS,
    Documento,
    cortar_por_artigos,
    cortar_por_secoes,
    data_do_cabecalho,
    dividir_texto,
    ingerir,
    juntar_linhas,
    montar_chunks,
    texto_de_html,
    texto_de_pdf,
)
from src.schema import Chunk, Sensibilidade

# ----------------------------------------------------------------- HTML


def test_html_cp1252_e_nbsp():
    html = "<p>Art.&nbsp;1º Esta lei não revoga nada.</p>".encode("cp1252")
    texto, _ = texto_de_html(html)
    assert texto == "Art. 1º Esta lei não revoga nada."


def test_quebra_de_linha_no_codigo_fonte_nao_separa_o_artigo_do_numero():
    # Caso real do Planalto: "Art." e "75." separados por uma quebra no código-fonte da página.
    html = b"<p><span>Art. \n\t75. \xc9 dispens\xe1vel a licita\xe7\xe3o:</span></p>"
    texto, _ = texto_de_html(html)
    assert texto == "Art. 75. É dispensável a licitação:"


def test_html_ignora_script_estilo_e_texto_riscado():
    html = (
        b"<html><head><title>x</title><style>p{}</style></head><body>"
        b"<script>var a=1;</script><p>Vale <strike>revogado</strike> isto.</p></body></html>"
    )
    texto, riscados = texto_de_html(html)
    assert "revogado" not in texto and "var a" not in texto and "p{}" not in texto
    assert "Vale" in texto and "isto." in texto
    assert riscados == 1


def test_strike_sem_fechar_nao_engole_o_resto_do_documento():
    texto, _ = texto_de_html(b"<p>antes <strike>riscado</p><p>depois continua</p>")
    assert "depois continua" in texto and "riscado" not in texto


def test_blocos_viram_linhas():
    texto, _ = texto_de_html(b"<p>um</p><p>dois</p><div>tres</div>")
    assert texto.splitlines() == ["um", "dois", "tres"]


# ----------------------------------------------------------------- PDF


def _pdf(tmp_path: Path, paginas: list[list[str]]) -> Path:
    caminho = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    for linhas in paginas:
        pagina = doc.new_page()
        y = 60
        for linha in linhas:
            pagina.insert_text((50, y), linha, fontsize=11)
            y += 18
    doc.save(caminho)
    doc.close()
    return caminho


def test_pdf_remove_cabecalho_e_rodape_de_navegador(tmp_path):
    paginas = [
        [
            "Leis Estaduais",
            "Rio de Janeiro",
            "18/09/26, 17:44",
            "Decreto 48820 2023 de Rio de Janeiro RJ",
            "https://leisestaduais.com.br/rj/decreto-n-48820",
            f"Art. {n} O conteudo do artigo numero {n}.",
            f"{n}/3",
        ]
        for n in (1, 2, 3)
    ]
    texto = texto_de_pdf(_pdf(tmp_path, paginas))
    assert "Leis Estaduais" not in texto and "leisestaduais.com.br" not in texto
    assert "17:44" not in texto and "1/3" not in texto
    assert all(f"Art. {n} O conteudo" in texto for n in (1, 2, 3))


def test_pdf_nao_remove_linha_unica_que_nao_se_repete(tmp_path):
    texto = texto_de_pdf(_pdf(tmp_path, [["Art. 1 Um.", "Texto normal"], ["Art. 2 Dois.", "Outro texto"], ["Art. 3 Tres."]]))
    assert "Texto normal" in texto and "Outro texto" in texto


# ----------------------------------------------------------------- estrutura


def test_juntar_linhas_une_o_paragrafo_e_preserva_a_estrutura():
    bruto = "Art. 1º Fica instituído o\nplano anual.\n§ 1º O plano\né público.\nI - primeiro;\nII - segundo."
    assert juntar_linhas(bruto).splitlines() == [
        "Art. 1º Fica instituído o plano anual.",
        "§ 1º O plano é público.",
        "I - primeiro;",
        "II - segundo.",
    ]


def test_juntar_linhas_nao_trata_data_como_inicio_de_secao():
    assert juntar_linhas("publicado em\n12 de março de 2023").splitlines() == ["publicado em 12 de março de 2023"]


def test_juntar_linhas_une_palavra_hifenizada():
    assert juntar_linhas("o regulamen-\ntar do plano") == "o regulamentar do plano"


TEXTO_LEI = """DECRETO Nº 1 DE 2 DE JANEIRO DE 2023
O GOVERNADOR decreta:
CAPÍTULO I
DAS DISPOSIÇÕES GERAIS
Art. 1º Fica instituído o plano.
Parágrafo único. Vale para todos.
Art. 2º Os órgãos aderem.
CAPÍTULO II
DA EXECUÇÃO
Seção I
Do Processo
Art. 3º Executa-se.
Art. 3º-A Acrescenta-se.
Art. 4. Fim."""


def test_corta_por_artigo_com_preambulo_e_hierarquia():
    secoes = cortar_por_artigos(TEXTO_LEI)
    assert [s.rotulo for s in secoes] == ["Preâmbulo", "Art. 1", "Art. 2", "Art. 3", "Art. 3-A", "Art. 4"]
    assert "GOVERNADOR" in secoes[0].texto
    assert secoes[1].hierarquia == ["CAPÍTULO I DAS DISPOSIÇÕES GERAIS"]
    assert "Parágrafo único" in secoes[1].texto  # o parágrafo fica dentro do artigo
    assert secoes[3].hierarquia == ["CAPÍTULO II DA EXECUÇÃO", "Seção I Do Processo"]
    assert secoes[3].artigo == "3" and secoes[4].artigo == "3-A"


def test_artigo_no_fim_da_linha_e_reconhecido():
    # Em PDFs de decretos, "Art. 1º" pode ficar sozinho na linha.
    assert [s.rotulo for s in cortar_por_artigos("Art. 1º\nTexto do artigo.\nArt. 2º\nOutro.")] == ["Art. 1", "Art. 2"]


def test_artigo_citado_no_meio_da_frase_nao_abre_artigo():
    secoes = cortar_por_artigos("Art. 1º Aplica-se o disposto no art. 75 da Lei.\nArt. 2º Fim.")
    assert [s.rotulo for s in secoes] == ["Art. 1", "Art. 2"]


def test_artigo_repetido_ganha_sufixo_para_nao_repetir_o_id():
    # Um decreto que altera outro cita, entre aspas, artigos do decreto alterado.
    secoes = cortar_por_artigos('Art. 1º Altera.\nArt. 2º Passa a vigorar:\n“Art. 1º Novo texto."\n')
    chaves = [s.chave for s in secoes]
    assert chaves == ["art1", "art2", "art1-r2"]
    assert len(set(chaves)) == len(chaves)


def test_corta_edital_por_secoes_numeradas():
    texto = (
        "EDITAL Nº 1\nO município faz saber.\n"
        "1. DO OBJETO\nAquisição de coisas.\n"
        "2. DA PARTICIPAÇÃO\nPodem participar todos.\n"
        "Em 12 de março de 2023 foi publicado.\n"
        "2.1 Não podem as inidôneas.\n"
    )
    secoes = cortar_por_secoes(texto)
    assert [s.rotulo for s in secoes] == ["Abertura", "1. DO OBJETO", "2. DA PARTICIPAÇÃO"]
    assert "12 de março de 2023" in secoes[2].texto  # data no meio do texto não abre seção
    assert [s.chave for s in secoes] == ["s000", "s001", "s002"]


# ----------------------------------------------------------------- divisão em partes


def test_texto_curto_nao_e_dividido():
    assert dividir_texto("curto") == ["curto"]


def test_texto_longo_respeita_o_limite_e_tem_sobreposicao():
    paragrafos = [f"Inciso {i}: " + ("palavra " * 30).strip() + "." for i in range(40)]
    partes = dividir_texto("\n".join(paragrafos), max_chars=800, overlap=100)
    assert len(partes) > 3
    assert all(len(p) <= 800 + 100 for p in partes)
    for anterior, seguinte in zip(partes, partes[1:], strict=False):
        cauda = anterior[-40:]
        assert cauda in seguinte, "cada parte deve começar repetindo o final da anterior"


def test_paragrafo_gigante_e_cortado_em_frases():
    gigante = " ".join(f"Frase numero {i} termina aqui." for i in range(200))
    partes = dividir_texto(gigante, max_chars=500, overlap=50)
    assert len(partes) > 5
    assert all(len(p) <= 600 for p in partes)


def test_divisao_e_deterministica():
    texto = "\n".join(f"Linha {i} com algum texto de preenchimento." for i in range(400))
    assert dividir_texto(texto) == dividir_texto(texto)


# ----------------------------------------------------------------- datas


@pytest.mark.parametrize(
    "cabecalho,esperado",
    [
        ("DECRETO Nº 48.820 DE 27 DE NOVEMBRO DE 2023", "2023-11-27"),
        ("DECRETO Nº 48.843 DE 13 DEZEMBRO DE 2023", "2023-12-13"),  # sem o "DE" antes do mês, como no texto real
        ("Decreto nº 49.129 de 05 de junho de 2024", "2024-06-05"),
    ],
)
def test_data_do_cabecalho_do_decreto(cabecalho, esperado):
    assert data_do_cabecalho(cabecalho) == esperado


def test_data_do_cabecalho_ausente():
    assert data_do_cabecalho("sem cabeçalho de decreto") is None


# ----------------------------------------------------------------- chunks


def _doc(**extra) -> Documento:
    base = dict(
        base_id="editais_normativo",
        caminho=Path("data/raw/rj/v1/editais_normativo/decreto-1.pdf"),
        stem="decreto-1",
        norma="Decreto RJ 1/2023",
        doc_type="decreto",
        data_publicacao="2023-01-02",
        origem="https://exemplo.gov.br/decreto-1",
    )
    base.update(extra)
    return Documento(**base)


def test_chunks_seguem_o_contrato_de_metadados():
    chunks, _ = montar_chunks(_doc(altera="Decreto RJ 0/2022"), cortar_por_artigos(TEXTO_LEI), Sensibilidade.PUBLICO)
    for chunk in chunks:
        Chunk.model_validate(chunk.model_dump())  # o contrato do schema
        m = chunk.metadata
        assert m.base_id == "editais_normativo" and m.sensitivity is Sensibilidade.PUBLICO
        assert m.data_publicacao == "2023-01-02" and m.altera == "Decreto RJ 0/2022"
    assert chunks[1].metadata.chunk_id == "editais_normativo:decreto-1:art1:p1"
    assert chunks[1].texto.startswith("Decreto RJ 1/2023 - Art. 1\n")


def test_chunk_ids_sao_unicos_e_longos_viram_partes():
    longo = "Art. 1º Início.\n" + "\n".join(f"I{i} - " + "texto " * 60 for i in range(30))
    chunks, _ = montar_chunks(_doc(), cortar_por_artigos(longo), Sensibilidade.PUBLICO)
    ids = [c.metadata.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)) and len(chunks) > 3
    assert ids[0].endswith(":p1") and ids[1].endswith(":p2")
    assert chunks[0].metadata.partes == len(chunks)
    assert all(len(c.texto) <= MAX_CHARS + 400 for c in chunks)


def test_dado_pessoal_e_mascarado_e_marcado():
    texto = "Art. 1º Contato do servidor: CPF 123.456.789-09, e-mail joao@gmail.com."
    chunks, com_dado = montar_chunks(_doc(), cortar_por_artigos(texto), Sensibilidade.PUBLICO)
    assert com_dado == 1
    assert "123.456.789-09" not in chunks[0].texto and "joao@gmail.com" not in chunks[0].texto
    assert chunks[0].metadata.contem_dado_pessoal is True


def test_chunk_sem_dado_pessoal_nao_e_marcado():
    chunks, com_dado = montar_chunks(_doc(), cortar_por_artigos("Art. 1º Texto limpo."), Sensibilidade.PUBLICO)
    assert com_dado == 0 and chunks[0].metadata.contem_dado_pessoal is False


# ----------------------------------------------------------------- ponta a ponta


@pytest.fixture()
def corpus(tmp_path):
    """Um mini-corpus coletado de verdade pelo fetch (arquivos locais, conferidos por hash)."""
    manual = tmp_path / "manual"
    manual.mkdir()
    html = "<p>Art. 1º Fica criado o plano.</p><p>Art. 2º Vale para todos.</p>".encode("cp1252")
    (manual / "lei.html").write_bytes(html)
    pdf = manual / "decreto.pdf"
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((50, 80), "DECRETO Nº 7 DE 5 DE MAIO DE 2023", fontsize=11)
    pagina.insert_text((50, 100), "Art. 1º Regulamenta a lei.", fontsize=11)
    doc.save(pdf)
    doc.close()
    manifesto = tmp_path / "corpus_manifest.yaml"
    manifesto.write_text(
        yaml.safe_dump({
            "scenario_id": "teste",
            "bases": [{
                "base_id": "editais_normativo",
                "natureza": "normativa",
                "sensitivity_default": "publico",
                "acesso": {
                    "tipo": "arquivo_local",
                    "arquivos_locais": {"pasta": str(manual), "arquivos": [
                        {"arquivo": "lei.html", "norma": "Lei 1/2020", "doc_type": "lei",
                         "data_publicacao": "2020-01-01", "sha256": hashlib.sha256(html).hexdigest(),
                         "origem": "https://exemplo.gov.br/lei"},
                        {"arquivo": "decreto.pdf", "norma": "Decreto 7/2023", "doc_type": "decreto",
                         "origem": "https://exemplo.gov.br/decreto"},
                    ]},
                },
            }],
        }),
        encoding="utf-8",
    )
    pastas = Pastas(dados=tmp_path / "data", cenario=tmp_path / "scenarios" / "teste", cenario_id="teste")
    fetch(manifesto, "v1", pastas, dormir=lambda _: None, escrever=lambda _: None)
    return manifesto, pastas


def test_ingestao_ponta_a_ponta(corpus):
    manifesto, pastas = corpus
    relatorio = ingerir(manifesto, "v1", "editais_normativo", pastas)

    saida = pastas.dados / "processed" / "teste" / "v1_editais_normativo.jsonl"
    linhas = [json.loads(l) for l in saida.read_text(encoding="utf-8").splitlines()]
    assert relatorio["total_chunks"] == len(linhas) == 4  # 2 artigos da lei + preâmbulo e artigo do decreto
    assert {l["metadata"]["doc_type"] for l in linhas} == {"lei", "decreto"}
    for l in linhas:
        Chunk.model_validate(l)
    # a data do decreto veio do próprio texto, porque o manifesto não a trazia
    decreto = [l for l in linhas if l["metadata"]["doc_type"] == "decreto"]
    assert {l["metadata"]["data_publicacao"] for l in decreto} == {"2023-05-05"}
    assert relatorio["avisos"] == []


def test_ingestao_e_deterministica(corpus):
    manifesto, pastas = corpus
    r1 = ingerir(manifesto, "v1", "editais_normativo", pastas)
    r2 = ingerir(manifesto, "v1", "editais_normativo", pastas)
    assert r1["sha256_do_jsonl"] == r2["sha256_do_jsonl"]


def test_aviso_quando_a_data_do_manifesto_difere_da_do_texto(corpus, tmp_path):
    manifesto, pastas = corpus
    dados = yaml.safe_load(manifesto.read_text(encoding="utf-8"))
    dados["bases"][0]["acesso"]["arquivos_locais"]["arquivos"][1]["data_publicacao"] = "2023-05-06"
    novo = tmp_path / "outro.yaml"
    novo.write_text(yaml.safe_dump(dados), encoding="utf-8")
    pastas2 = Pastas(dados=tmp_path / "data2", cenario=tmp_path / "sc2", cenario_id="teste")
    fetch(novo, "v1", pastas2, dormir=lambda _: None, escrever=lambda _: None)

    relatorio = ingerir(novo, "v1", "editais_normativo", pastas2)
    assert any("difere" in aviso for aviso in relatorio["avisos"])


def test_base_sem_documentos_coletados_da_erro(corpus):
    manifesto, pastas = corpus
    with pytest.raises(FileNotFoundError):
        ingerir(manifesto, "v9", "editais_normativo", pastas)


# ----------------------------------------------------------------- cortador de itens (resoluções sem artigos, instruções, chamadas)

from src.ingest import Documento, cortar_documento, cortar_por_itens  # noqa: E402

RESOLUCAO_SEM_ARTIGOS = """RESOLUÇÃO Nº 1, DE 12 DE DEZEMBRO DE 2012.
O Plenário resolve:
I – DAS DISPOSIÇÕES PRELIMINARES
A presente Resolução incorpora referenciais.
II - DOS TERMOS E DEFINIÇÕES
II.1 - achados da pesquisa - fatos encontrados pelo pesquisador no
decurso da pesquisa;
II.2 - assentimento - anuência do participante;
II.2.1 - assentimento livre - sem coação;
III - DOS ASPECTOS ÉTICOS
III.1 - A eticidade da pesquisa implica em:
a) respeito ao participante;
b) ponderação entre riscos e benefícios.
III.2 - Toda pesquisa deve ser aprovada.
"""


def test_itens_de_resolucao_sem_artigos_ficam_sob_a_secao_certa():
    s = cortar_por_itens(RESOLUCAO_SEM_ARTIGOS)
    por_numero = {x.artigo: x for x in s if x.artigo}
    assert list(por_numero) == ["II.1", "II.2", "III.1", "III.2"]
    assert por_numero["II.1"].hierarquia == ["II - DOS TERMOS E DEFINIÇÕES"]
    assert por_numero["III.2"].hierarquia == ["III - DOS ASPECTOS ÉTICOS"]
    assert "decurso da pesquisa" in por_numero["II.1"].texto  # a linha quebrada volta para o item
    assert "II.2.1" in por_numero["II.2"].texto  # subitem fica dentro do item
    assert "b) ponderação" in por_numero["III.1"].texto  # alíneas ficam dentro do item
    assert s[0].rotulo == "Preâmbulo" and "Plenário" in s[0].texto


def test_chave_do_item_romano_nao_se_confunde_entre_ii_e_iii():
    chaves = [x.chave for x in cortar_por_itens(RESOLUCAO_SEM_ARTIGOS)]
    assert "item-ii-1" in chaves and "item-iii-1" in chaves and len(chaves) == len(set(chaves))


CHAMADA_COM_TRAVESSAO = """Chamada Nº 10/2023 - UNIVERSAL
O Conselho torna pública a presente Chamada.
1 – Objeto
1.1 – Apoiar projetos de pesquisa em qualquer área.
1.2 - As propostas deverão ser inseridas nas faixas:
1.2.1 - Faixa A - Grupos Emergentes, limitados a R$ 165.000,00.
2 – Cronograma
Lançamento da Chamada                21/06/2023
3 – Critérios de Elegibilidade
3.1 – Os critérios são obrigatórios.
"""


def test_chamada_com_titulos_e_itens_com_travessao():
    s = cortar_por_itens(CHAMADA_COM_TRAVESSAO)
    assert [x.artigo for x in s if x.artigo] == ["1.1", "1.2", "3.1"]
    assert [x.rotulo for x in s if not x.artigo] == ["Preâmbulo", "1 – Objeto", "2 – Cronograma", "3 – Critérios de Elegibilidade"]
    assert "21/06/2023" in next(x for x in s if x.rotulo == "2 – Cronograma").texto
    assert "165.000,00" in next(x for x in s if x.artigo == "1.2").texto


INSTRUCOES_COM_SUMARIO = """INSTRUÇÕES PARA PRESTAÇÃO DE CONTAS
SUMÁRIO
1  ORIENTAÇÕES GERAIS ....................................... 6
1.1  O dever de prestar contas ................................ 6
1.2    Apresentação da Prestação de Contas (Relatório Técnico e
Financeiro) ......................... 7
1     ORIENTAÇÕES GERAIS      (voltar ao sumário)
1.1    O dever de prestar contas    (voltar ao sumário)
1.1.1 Todo beneficiário deve prestar contas.
1.2     Apresentação da Prestação de Contas    (voltar ao sumário)
1.2.1 A prestação ocorre no sistema. Veja o item 1.4
1.4 destas Instruções para detalhes.
1.4      Atos vedados ao outorgado   (voltar ao sumário)
a) Utilizar recursos em despesas não previstas.
"""


def test_sumario_com_pontilhado_remissao_e_referencia_cruzada_nao_viram_itens():
    s = cortar_por_itens(INSTRUCOES_COM_SUMARIO)
    itens = [x for x in s if x.artigo]
    assert [x.artigo for x in itens] == ["1.1", "1.2", "1.4"]  # sem 1.4 duplicado da remissão
    assert "(voltar ao sumário)" not in " ".join(x.texto + x.rotulo for x in s)
    assert "destas Instruções para detalhes" in itens[1].texto  # a remissão ficou dentro do item 1.2
    assert all("...." not in x.texto for x in s)
    assert s[0].hierarquia == [] and any(x.rotulo.startswith("1     ORIENTAÇÕES") or "ORIENTAÇÕES GERAIS" in x.rotulo for x in s)


def test_item_curto_repetido_do_sumario_e_descartado_mas_o_do_corpo_fica():
    texto = "1.2    Apresentação da\n1.1 Primeiro item com texto suficiente para ficar, de verdade.\n" + "1.2 Apresentação da Prestação de Contas " + "x" * 300
    itens = [x for x in cortar_por_itens(texto) if x.artigo == "1.2"]
    assert len(itens) == 1 and len(itens[0].texto) > 300


def _doc_tipo(doc_type):
    from pathlib import Path

    return Documento("b", Path("x.pdf"), "x", "Norma", doc_type, "2026-01-01", "http://x")


def test_norma_etica_com_artigos_usa_artigos_e_sem_artigos_usa_itens():
    com_artigos = "Preâmbulo\nArt. 1º Primeiro.\nArt. 2º Segundo.\nArt. 3º Terceiro.\nArt. 4º Quarto.\n"
    assert [x.rotulo for x in cortar_documento(_doc_tipo("norma_etica"), com_artigos)][1:3] == ["Art. 1", "Art. 2"]
    assert any(x.artigo == "II.1" for x in cortar_documento(_doc_tipo("norma_etica"), RESOLUCAO_SEM_ARTIGOS))


def test_edital_sem_secoes_em_maiusculas_cai_nos_itens_e_instrucao_vai_direto_aos_itens():
    assert any(x.artigo == "1.1" for x in cortar_documento(_doc_tipo("edital"), CHAMADA_COM_TRAVESSAO))
    assert any(x.artigo == "1.4" for x in cortar_documento(_doc_tipo("instrucao"), INSTRUCOES_COM_SUMARIO))


def test_edital_com_secoes_em_maiusculas_continua_usando_secoes():
    texto = "Abertura do edital.\n1. OBJETIVOS\nTexto do objetivo.\n2. ELEGIBILIDADE E RESTRIÇÕES\nTexto da elegibilidade.\n"
    assert [x.rotulo for x in cortar_documento(_doc_tipo("edital"), texto)] == ["Abertura", "1. OBJETIVOS", "2. ELEGIBILIDADE E RESTRIÇÕES"]


def test_pdf_sem_extensao_e_reconhecido_pelo_conteudo(tmp_path):
    import pymupdf

    from src.ingest import texto_do_documento

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Texto do edital")
    caminho = tmp_path / "arquivo_sem_extensao"
    caminho.write_bytes(pdf.tobytes())
    texto, _ = texto_do_documento(Documento("b", caminho, "x", "Norma", "edital", None, "http://x"))
    assert "Texto do edital" in texto


# ----------------------------------------------------------------- PDF de duas colunas


def _pdf_de(tmp_path, desenhar):
    import pymupdf

    doc = pymupdf.open()
    desenhar(doc.new_page())
    caminho = tmp_path / "x.pdf"
    doc.save(caminho)
    doc.close()
    return caminho


def test_pagina_de_duas_colunas_le_a_esquerda_inteira_e_depois_a_direita(tmp_path):
    from src.ingest import texto_de_pdf

    def desenhar(pagina):
        for i in range(24):
            pagina.insert_text((50, 80 + i * 16), f"esq{i:02d} um dois tres quatro cinco seis", fontsize=9)
            pagina.insert_text((330, 80 + i * 16), f"dir{i:02d} sete oito nove dez onze doze", fontsize=9)

    palavras = texto_de_pdf(_pdf_de(tmp_path, desenhar)).split()
    marcadores = [w for w in palavras if w.startswith(("esq", "dir"))]
    assert marcadores == [f"esq{i:02d}" for i in range(24)] + [f"dir{i:02d}" for i in range(24)]


def test_linha_que_atravessa_as_duas_colunas_nao_mistura_o_texto_de_uma_com_o_da_outra(tmp_path):
    """O defeito original: o texto saía como 'processo de educação ... seja realiza- ... indispensáveis'."""
    from src.ingest import texto_de_pdf

    def desenhar(pagina):
        for i in range(20):
            pagina.insert_text((50, 80 + i * 16), f"A{i:02d} aaaa bbbb cccc dddd eeee ffff", fontsize=9)
            pagina.insert_text((330, 80 + i * 16), f"B{i:02d} gggg hhhh iiii jjjj kkkk llll", fontsize=9)

    linhas = texto_de_pdf(_pdf_de(tmp_path, desenhar)).splitlines()
    assert not any("A" in l.split()[0] and "B" in l for l in linhas if l.split())  # nenhuma linha tem texto das duas colunas
    assert linhas[0].startswith("A00") and any(l.startswith("B00") for l in linhas)


def test_pagina_de_coluna_unica_nao_e_reordenada(tmp_path):
    from src.ingest import texto_de_pdf

    def desenhar(pagina):
        for i in range(30):
            pagina.insert_text((50, 60 + i * 16), f"linha{i:02d} um dois tres quatro cinco seis sete oito nove dez onze doze treze", fontsize=9)

    marcadores = [w for w in texto_de_pdf(_pdf_de(tmp_path, desenhar)).split() if w.startswith("linha")]
    assert marcadores == [f"linha{i:02d}" for i in range(30)]


def test_pagina_curta_nao_entra_no_caminho_de_colunas(tmp_path):
    from src.ingest import texto_de_pdf

    def desenhar(pagina):
        pagina.insert_text((50, 80), "Esquerda um dois", fontsize=9)
        pagina.insert_text((330, 80), "Direita tres quatro", fontsize=9)

    assert "Esquerda" in texto_de_pdf(_pdf_de(tmp_path, desenhar))


# --------------------------------------------------------------------------- reconhecimento de nomes (NER) na ingestão


class NerFalso:
    """Troca a palavra 'Art.' por [NOME], só para ver se a ingestão o chamou e o que fez com o resultado."""

    def __init__(self) -> None:
        self.chamadas = 0

    def mascarar_nomes(self, texto: str) -> tuple[str, int]:
        self.chamadas += 1
        return texto.replace("Art.", "[NOME]"), texto.count("Art.")


def _como_conversacional(manifesto: Path) -> Path:
    dados = yaml.safe_load(manifesto.read_text(encoding="utf-8"))
    dados["bases"][0]["natureza"] = "conversacional"
    novo = manifesto.with_name("conversacional.yaml")
    novo.write_text(yaml.safe_dump(dados), encoding="utf-8")
    return novo


def test_ner_nao_toca_em_base_normativa(corpus):
    manifesto, pastas = corpus
    ner = NerFalso()
    relatorio = ingerir(manifesto, "v1", "editais_normativo", pastas, ner)
    assert ner.chamadas == 0
    assert relatorio["nomes_redigidos_por_ner"] == 0


def test_ner_age_em_base_conversacional_e_entra_no_relatorio(corpus):
    manifesto, pastas = corpus
    ner = NerFalso()
    relatorio = ingerir(_como_conversacional(manifesto), "v1", "editais_normativo", pastas, ner)
    assert ner.chamadas == 2  # um por documento
    assert relatorio["nomes_redigidos_por_ner"] > 0
    assert any(d.get("nomes_redigidos") for d in relatorio["documentos"])
    saida = (pastas.dados / "processed" / "teste" / "v1_editais_normativo.jsonl").read_text(encoding="utf-8")
    assert "[NOME]" in saida


def test_sem_ner_o_relatorio_diz_que_nao_houve(corpus):
    manifesto, pastas = corpus
    assert ingerir(manifesto, "v1", "editais_normativo", pastas)["nomes_redigidos_por_ner"] is None


def test_linha_de_comando_recusa_ner_ligado_sem_os_arquivos(corpus, monkeypatch, capsys):
    import src.ingest as mod
    from src.ner import NerIndisponivel

    manifesto, pastas = corpus
    monkeypatch.setattr(mod, "pastas_do_cenario", lambda _: pastas)
    monkeypatch.setattr(mod, "versoes_existentes", lambda _: ["v1"])
    monkeypatch.setattr(mod, "get_settings", lambda: type("S", (), {"redacao_ner": True})())

    def sem_modelo():
        raise NerIndisponivel("faltam arquivos")

    monkeypatch.setattr("src.ner.carregar_ner", sem_modelo)
    assert mod.main(["--scenario", "rj", "--manifest", str(manifesto)]) == 2
    assert "REDACAO_NER=true" in capsys.readouterr().err
