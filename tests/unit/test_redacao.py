"""Testes da redação de trechos identificadores (revisor que fala de si) e da política declarada em capacidades.yaml."""

from __future__ import annotations

import json

import pytest

from src.capacidades import CapacidadesInvalidas, capacidades_do_cenario, interpretar
from src.fetch_corpus import Pastas
from src.ingest import ingerir
from src.redacao import compilar, redigir
from src.synth import construir

REGRAS = compilar(list(capacidades_do_cenario("pesquisa").redacoes_na_ingestao))
MARCA = "[TRECHO REMOVIDO: pode identificar o revisor]"

# Frases do primeiro teste (escritas junto com as regras) e frases novas, avaliadas depois. Ver PROGRESSO: a primeira
# medição, antes de generalizar as regras, foi 0 de 8 nas frases fora do gabarito.
AUTORREFERENCIA = [
    "Como demonstrei em Quintanilha e Prado (2019), o uso de janelas curtas distorce a estimativa.",
    "No Laboratório de Sistemas Complexos da UFFV, onde atuo, adotamos há anos esse protocolo.",
    "Em meu grupo no IFPA-X já testamos essa hipótese e o efeito não se sustentou.",
    "Costumo dizer aos meus orientandos que correlação é apenas o começo da conversa.",
    "Declaro ter colaborado, em 2021, com o grupo citado na seção 2.",
    "Esta revisão não cita o nosso artigo de 2020, que reúne os biomarcadores discutidos.",
    "Na nossa UFFV, os protocolos de coleta seguem outro critério.",
    "Fui coordenador do edital avaliado e por isso conheço bem os dados de execução.",
    "Já orientei três dissertações sobre esse tema.",
    "Trabalho com esse conjunto de dados há dez anos.",
    "Sou autor de um dos artigos citados na introdução.",
    "Em minha experiência como editor associado, esse tipo de resumo costuma ser rejeitado.",
    "Participei da banca de qualificação do grupo mencionado.",
    "Este mesmo método foi aplicado por nós em 2018 na Universidade Federal Fictícia do Vale.",
    "Como pesquisador da área há décadas, reconheço o estilo dos autores.",
    "Orientei dois mestrandos que trabalharam com essa mesma técnica.",
    "Sou professor de estatística e conheço bem esse problema.",
    "Há mais de quinze anos pesquiso séries temporais em sensores.",
    "Participei de um projeto semelhante financiado pela mesma agência.",
    "Em minha experiência como parecerista, a amostra mínima é maior.",
    "Publicamos resultados parecidos em 2017.",
    "Visitei o laboratório dos autores no ano passado.",
]
# Críticas técnicas e frases que parecem, mas não são, autorreferência: nada disso pode ser apagado.
CONTROLES = [
    "A amostra é pequena e não há cálculo de poder estatístico que justifique o tamanho escolhido.",
    "O código e os dados não estão disponíveis, o que impede a reprodução dos resultados.",
    "Sugiro revisar o resumo, que repete trechos da introdução.",
    "Recomendo que os autores discutam melhor o viés de seleção.",
    "Considero que a conclusão extrapola o que os dados permitem afirmar.",
    "Os autores trabalham com séries temporais há muitos anos, segundo a introdução.",
    "A Figura 3 foi publicada anteriormente pelos autores em 2018.",
    "O método foi aplicado em 2019 pelos próprios autores.",
    "Não há menção à aprovação por comitê de ética em pesquisa.",
]


@pytest.mark.parametrize("frase", AUTORREFERENCIA)
def test_frase_em_que_o_revisor_fala_de_si_e_redigida(frase):
    texto, feitas = redigir(frase, "parecer", REGRAS)
    assert texto == MARCA and feitas == {"pista_de_identidade_do_revisor": 1}


@pytest.mark.parametrize("frase", CONTROLES)
def test_critica_tecnica_nao_e_apagada(frase):
    assert redigir(frase, "parecer", REGRAS) == (frase, {})


def test_so_a_frase_identificadora_some_o_resto_do_paragrafo_fica():
    texto, feitas = redigir("A amostra é pequena. Em minha experiência, isso é comum. Falta o código.", "parecer", REGRAS)
    assert texto == f"A amostra é pequena. {MARCA} Falta o código." and sum(feitas.values()) == 1


def test_a_regra_so_vale_para_os_tipos_de_documento_declarados():
    frase = "Já orientei três dissertações sobre esse tema."
    assert redigir(frase, "correspondencia_editorial", REGRAS) == (frase, {})
    assert redigir(frase, "parecer", REGRAS)[1]


def test_acento_e_caixa_nao_atrapalham():
    assert redigir("EM MINHA EXPERIENCIA isso falha.", "parecer", REGRAS)[1]
    assert redigir("Já ORIENTEI três teses.", "parecer", REGRAS)[1]


def test_limite_conhecido_mencao_em_terceira_pessoa_a_uma_instituicao_nao_e_redigida():
    """Ambíguo de propósito: 'a equipe da UFFV' pode ser sobre os autores, e não sobre o revisor. Se isto passar a ser
    redigido, atualize o teste e o PROGRESSO: a regra mudou de natureza."""
    for frase in ("A equipe da UFFV deveria ter citado a literatura recente.", "O grupo da UEEP já havia reportado esse efeito."):
        assert redigir(frase, "parecer", REGRAS)[1] == {}


# ----------------------------------------------------------------- na ingestão, com a base sintética real


@pytest.fixture()
def pastas_com_pareceres(tmp_path):
    dados = tmp_path / "data"
    construir("pesquisa", dados / "synthetic" / "pesquisa" / "pareceres_conversacional")
    manifesto = tmp_path / "m.yaml"
    manifesto.write_text(
        "scenario_id: pesquisa\nbases:\n  - base_id: pareceres_conversacional\n    natureza: conversacional\n    acesso: {tipo: sintetico}\n"
        "    sensitivity_default: restrito\n", encoding="utf-8")
    return manifesto, Pastas(dados=dados, cenario=tmp_path / "sc", cenario_id="pesquisa")


def test_ingestao_redige_as_pistas_e_nada_do_gabarito_chega_ao_indice(pastas_com_pareceres):
    manifesto, pastas = pastas_com_pareceres
    relatorio = ingerir(manifesto, "v2", "pareceres_conversacional", pastas)
    base = pastas.dados / "synthetic" / "pesquisa" / "pareceres_conversacional"
    gabarito = json.loads((base / "_gabarito.json").read_text(encoding="utf-8"))
    jsonl = (pastas.dados / "processed" / "pesquisa" / "v2_pareceres_conversacional.jsonl").read_text(encoding="utf-8")

    pistas = [f for arm in gabarito.values() for a in arm for f in a.get("pistas", [])]
    assert pistas and all(f not in jsonl for f in pistas)
    for arm in gabarito.values():
        for a in arm:
            for chave in ("revisor_real", "instituicao_real"):
                assert chave not in a or a[chave] not in jsonl
    assert relatorio["frases_redigidas_por_categoria"] == {"pista_de_identidade_do_revisor": len(pistas)}
    assert "UFFV" not in jsonl and "IFPA-X" not in jsonl and "UEEP" not in jsonl


def test_ingestao_marca_cada_trecho_como_sintetico_conversacional_e_restrito(pastas_com_pareceres):
    manifesto, pastas = pastas_com_pareceres
    ingerir(manifesto, "v2", "pareceres_conversacional", pastas)
    linhas = [json.loads(l) for l in (pastas.dados / "processed" / "pesquisa" / "v2_pareceres_conversacional.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(linhas) == 21
    for l in linhas:
        m = l["metadata"]
        assert m["sintetico"] is True and m["natureza"] == "conversacional" and m["sensitivity"] == "restrito"
    parecer = next(l for l in linhas if l["metadata"].get("manuscrito_id") == "M-014" and l["metadata"].get("revisor_codigo") == "revisor 2")
    assert "amostra é pequena" in parecer["texto"] and "comitê de ética" in parecer["texto"]  # a crítica técnica sobrevive


def test_base_sintetica_ausente_diz_como_gerar(tmp_path):
    manifesto = tmp_path / "m.yaml"
    manifesto.write_text("scenario_id: pesquisa\nbases:\n  - base_id: pareceres_conversacional\n    natureza: conversacional\n    acesso: {tipo: sintetico}\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="src.synth build"):
        ingerir(manifesto, "v2", "pareceres_conversacional", Pastas(dados=tmp_path / "data", cenario=tmp_path / "sc", cenario_id="pesquisa"))


# ----------------------------------------------------------------- política em capacidades.yaml


def _base(**extra):
    d = {"cenario": "x", "entidades": {"e": {"exibir": ["a"], "ordem_padrao": "a", "campos": [{"nome": "a", "tipo": "texto"}]}}}
    d.update(extra)
    return d


def test_niveis_consultaveis_padrao_e_so_o_publico():
    assert interpretar(_base()).niveis_consultaveis == ("publico",)
    assert capacidades_do_cenario("rj").niveis_consultaveis == ("publico", "interno")
    assert capacidades_do_cenario("pesquisa").niveis_consultaveis == ("publico", "restrito")


@pytest.mark.parametrize("extra,trecho", [
    ({"niveis_consultaveis": ["secreto"]}, "niveis_consultaveis"),
    ({"regras_de_saida": [{"categoria": "c", "mensagem": "m", "padroes": ["(["]}]}, "regra de saída"),
    ({"regras_de_saida": [{"categoria": "c"}]}, "regra de saída"),
    ({"redacoes_na_ingestao": [{"categoria": "c", "padroes": ["(["]}]}, "redação na ingestão"),
    ({"redacoes_na_ingestao": [{"categoria": "c", "padroes": []}]}, "redação na ingestão"),
])
def test_politica_invalida_e_recusada_ao_carregar(extra, trecho):
    with pytest.raises(CapacidadesInvalidas, match=trecho):
        interpretar(_base(**extra))
