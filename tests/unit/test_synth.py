"""Testes das bases conversacionais sintéticas."""

from __future__ import annotations

import json
import re

import pytest

from src.pii import mascarar_dados_pessoais
from src.synth import AVISO, BASES, construir, gerar_pesquisa, gerar_rj, verificar


@pytest.fixture(params=sorted(BASES))
def base(request, tmp_path):
    destino = tmp_path / "base"
    manifesto = construir(request.param, destino)
    return request.param, destino, manifesto


def test_mesma_semente_gera_os_mesmos_bytes_e_outra_semente_gera_outros(tmp_path):
    a = construir("rj", tmp_path / "a", 7)
    b = construir("rj", tmp_path / "b", 7)
    c = construir("rj", tmp_path / "c", 8)
    assert a["arquivos"] == b["arquivos"]
    assert a["arquivos"] != c["arquivos"]


def test_verificar_confirma_o_que_foi_gerado_e_acusa_arquivo_alterado(base):
    cenario, pasta, _ = base
    assert verificar(cenario, pasta) == []
    alvo = next(p for p in sorted(pasta.glob("*.txt")))
    alvo.write_text(alvo.read_text(encoding="utf-8") + "\nlinha de fora", encoding="utf-8")
    problemas = verificar(cenario, pasta)
    assert len(problemas) == 1 and alvo.name in problemas[0] and "alterado" in problemas[0]


def test_todo_documento_se_declara_sintetico(base):
    _, pasta, _ = base
    textos = [p.read_text(encoding="utf-8") for p in pasta.glob("*.txt")]
    assert textos and all(t.startswith(AVISO) for t in textos)
    indice = json.loads((pasta / "_indice.json").read_text(encoding="utf-8"))
    assert all(d["sintetico"] is True for d in indice)


def test_indice_tem_um_item_por_documento_com_o_minimo_de_metadados(base):
    _, pasta, manifesto = base
    indice = json.loads((pasta / "_indice.json").read_text(encoding="utf-8"))
    assert len(indice) == manifesto["documentos"] == len(list(pasta.glob("*.txt")))
    for d in indice:
        assert {"arquivo", "doc_type", "titulo", "data", "sintetico"} <= set(d) and (pasta / d["arquivo"]).is_file()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["data"])


def test_so_usa_dominios_reservados_para_email(base):
    _, pasta, _ = base
    for p in pasta.glob("*.txt"):
        for dominio in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", p.read_text(encoding="utf-8")):
            assert dominio.endswith(("example.org", "example.com")), (p.name, dominio)


def test_gabarito_aponta_para_valores_que_realmente_estao_no_texto(base):
    _, pasta, _ = base
    gabarito = json.loads((pasta / "_gabarito.json").read_text(encoding="utf-8"))
    assert gabarito
    for arquivo, armadilhas in gabarito.items():
        texto = (pasta / arquivo).read_text(encoding="utf-8")
        for a in armadilhas:
            for valor in a.get("valores", []) + a.get("pistas", []):
                assert valor in texto, (arquivo, valor)


def test_o_gabarito_nao_entra_no_indice_nem_no_texto_dos_documentos(base):
    """O que a ingestão lê (_indice.json e os .txt) não pode conter a resposta da armadilha."""
    _, pasta, _ = base
    gabarito = json.loads((pasta / "_gabarito.json").read_text(encoding="utf-8"))
    indice = (pasta / "_indice.json").read_text(encoding="utf-8")
    for armadilhas in gabarito.values():
        for a in armadilhas:
            for chave in ("revisor_real", "instituicao_real"):
                if chave in a:
                    assert a[chave] not in indice
                    for p in pasta.glob("*.txt"):
                        assert a[chave] not in p.read_text(encoding="utf-8")  # a identidade nunca aparece escrita, só as pistas


def test_rj_tem_as_cinco_armadilhas_do_desenho():
    tipos = sorted(a["tipo"] for d in gerar_rj(1) for a in d.armadilhas)
    assert tipos == ["credencial", "dado_bancario", "dado_pessoal", "dado_pessoal", "pedido_de_juizo"]


def test_pesquisa_tem_o_m014_com_tres_revisores_e_as_armadilhas_do_desenho():
    docs = gerar_pesquisa(1)
    m014 = [d for d in docs if d.doc_type == "parecer" and d.meta["manuscrito_id"] == "M-014"]
    assert sorted(d.meta["revisor_codigo"] for d in m014) == ["revisor 1", "revisor 2", "revisor 3"]
    tipos = {a["tipo"] for d in docs for a in d.armadilhas}
    assert tipos == {"pista_de_identidade", "pedido_de_identidade", "dado_pessoal"}
    r2 = next(d for d in m014 if d.meta["revisor_codigo"] == "revisor 2")
    assert r2.armadilhas and "amostra é pequena" in r2.texto and "comitê de ética" in r2.texto  # conteúdo que o PE-06 consulta


def test_o_texto_dos_pareceres_chama_o_revisor_so_pelo_codigo(base):
    cenario, pasta, _ = base
    if cenario != "pesquisa":
        pytest.skip("só vale para os pareceres")
    for p in pasta.glob("pe-*.txt"):
        texto = p.read_text(encoding="utf-8")
        if "PARECER DE REVISÃO" in texto:
            assert re.search(r"Revisor: revisor \d", texto) and "Dra." not in texto.split("\n\n")[1]


def test_valores_sensiveis_plantados_sao_mascarados_pela_ingestao(tmp_path):
    """As armadilhas de dado pessoal, credencial e dado bancário somem com o mascaramento padrão do projeto."""
    construir("rj", tmp_path / "rj")
    gabarito = json.loads((tmp_path / "rj" / "_gabarito.json").read_text(encoding="utf-8"))
    for arquivo, armadilhas in gabarito.items():
        mascarado = mascarar_dados_pessoais((tmp_path / "rj" / arquivo).read_text(encoding="utf-8"))
        for a in armadilhas:
            if a["tipo"] in ("dado_pessoal", "credencial", "dado_bancario"):
                for valor in a["valores"]:
                    parte = valor.split(", ")[-1] if a["tipo"] == "dado_bancario" else valor
                    assert parte not in mascarado, (arquivo, valor)


def test_cenario_sem_base_sintetica_da_erro_claro(tmp_path):
    with pytest.raises(ValueError, match="não tem base sintética"):
        construir("inexistente", tmp_path / "x")
