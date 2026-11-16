"""Testes da normalização: páginas JSON (API e snapshot) para tabelas SQL."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from src.fetch_corpus import Pastas
from src.normalize import linha_contratacao, linha_contrato, normalizar


@pytest.fixture()
def pastas(tmp_path) -> Pastas:
    return Pastas(dados=tmp_path / "data", cenario=tmp_path / "sc", cenario_id="teste")


def _manifesto(tmp_path) -> Path:
    caminho = tmp_path / "m.yaml"
    caminho.write_text(
        yaml.safe_dump({
            "scenario_id": "teste",
            "bases": [
                {"base_id": "api", "natureza": "estruturada", "acesso": {"tipo": "rest_api"}},
                {"base_id": "snap", "natureza": "estruturada", "acesso": {"tipo": "snapshot_sqlite"}},
                {"base_id": "leis", "natureza": "normativa", "acesso": {"tipo": "http_pdf"}},
            ],
        }),
        encoding="utf-8",
    )
    return caminho


def _contratacao(numero="00038166000105-1-000322/2026", atualizacao="2026-05-21T11:16:46", **extra):
    base = {
        "numeroControlePNCP": numero,
        "orgaoEntidade": {"cnpj": "00038166000105", "razaoSocial": "BANCO CENTRAL DO BRASIL", "poderId": "E", "esferaId": "F"},
        "unidadeOrgao": {"ufSigla": "RJ", "municipioNome": "Rio de Janeiro", "codigoUnidade": "1", "nomeUnidade": "Sede", "codigoIbge": "3304557"},
        "anoCompra": 2026, "sequencialCompra": 322, "numeroCompra": "108", "processo": "299813",
        "modalidadeId": 8, "modalidadeNome": "Dispensa", "modoDisputaId": 5, "modoDisputaNome": "Não se aplica",
        "situacaoCompraId": 1, "situacaoCompraNome": "Divulgada no PNCP", "srp": False,
        "objetoCompra": "Desumidificador de 30 litros", "valorTotalEstimado": 12274.0, "valorTotalHomologado": None,
        "amparoLegal": {"codigo": 20, "nome": "Lei 14.133/2021, Art. 75, III, a"},
        "dataPublicacaoPncp": "2026-05-21T11:13:46", "dataAtualizacaoGlobal": atualizacao,
        "dataAberturaProposta": None, "dataEncerramentoProposta": None,
    }
    base.update(extra)
    return base


def _contrato(numero="00038166000105-2-000117/2026", compra="00038166000105-1-000322/2026", **extra):
    base = {
        "numeroControlePNCP": numero, "numeroControlePncpCompra": compra,
        "orgaoEntidade": {"cnpj": "00038166000105", "razaoSocial": "BANCO CENTRAL DO BRASIL", "poderId": "E", "esferaId": "F"},
        "unidadeOrgao": {"ufSigla": "RJ", "municipioNome": "Rio de Janeiro", "codigoUnidade": "1", "nomeUnidade": "Sede", "codigoIbge": "3304557"},
        "anoContrato": 2026, "sequencialContrato": 117, "numeroContratoEmpenho": "90215", "processo": "299391",
        "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"}, "categoriaProcesso": {"id": 2, "nome": "Compras"},
        "objetoContrato": "AQUISIÇÃO DE CÉDULAS", "tipoPessoa": "PJ", "niFornecedor": "34164319000506",
        "nomeRazaoSocialFornecedor": "CASA DA MOEDA DO BRASIL", "valorInicial": 100.0, "valorGlobal": 631959574.8,
        "valorAcumulado": None, "valorParcela": 105326595.8, "numeroParcelas": 6,
        "dataAssinatura": "2026-06-22", "dataVigenciaInicio": "2026-06-22", "dataVigenciaFim": "2026-12-31",
        "dataPublicacaoPncp": "2026-06-23T17:02:32", "dataAtualizacaoGlobal": "2026-06-23T17:02:32",
    }
    base.update(extra)
    return base


def _pagina(pastas: Pastas, base: str, caminho: str, itens: list[dict]) -> None:
    arquivo = pastas.versao("v1") / base / caminho
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps({"data": itens}, ensure_ascii=False), encoding="utf-8")


def _linhas(pastas: Pastas, consulta: str, params=()):
    banco = pastas.dados / "normalized" / "teste" / "v1.sqlite"
    conexao = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    conexao.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conexao.execute(consulta, params)]
    finally:
        conexao.close()


# ----------------------------------------------------------------- conversão de um registro


def test_contratacao_campos_e_tipos():
    linha = linha_contratacao(_contratacao(), "api")
    assert linha["numero_controle_pncp"] == "00038166000105-1-000322/2026"
    assert linha["uf_sigla"] == "RJ" and linha["esfera_id"] == "F" and linha["modalidade_nome"] == "Dispensa"
    assert linha["valor_total_estimado"] == 12274.0 and linha["valor_total_homologado"] is None
    assert linha["data_publicacao"] == "2026-05-21" and linha["srp"] == 0
    assert linha["amparo_legal_nome"].startswith("Lei 14.133/2021")


def test_data_inexistente_vira_vazio_e_nao_derruba():
    assert linha_contratacao(_contratacao(dataPublicacaoPncp="2026-13-45T00:00:00"), "api")["data_publicacao"] is None
    assert linha_contratacao(_contratacao(dataPublicacaoPncp="lixo"), "api")["data_publicacao"] is None


def test_fornecedor_pessoa_fisica_tem_o_cpf_mascarado():
    linha, mascarou = linha_contrato(_contrato(tipoPessoa="PF", niFornecedor="12345678909"), "api")
    assert mascarou and linha["ni_fornecedor"] == "[CPF]" and linha["fornecedor_pessoa_fisica"] == 1
    assert linha["nome_fornecedor"] == "CASA DA MOEDA DO BRASIL"  # o nome do contratado é público


def test_cnpj_e_identificador_estrangeiro_nao_sao_mascarados():
    pj, _ = linha_contrato(_contrato(), "api")
    pe, _ = linha_contrato(_contrato(tipoPessoa="PE", niFornecedor="123456789"), "api")
    assert pj["ni_fornecedor"] == "34164319000506" and pj["fornecedor_pessoa_fisica"] == 0
    assert pe["ni_fornecedor"] == "123456789" and pe["fornecedor_pessoa_fisica"] == 0


def test_tipo_de_pessoa_ausente_com_cpf_valido_e_mascarado():
    linha, _ = linha_contrato(_contrato(tipoPessoa=None, niFornecedor="12345678909"), "api")
    assert linha["ni_fornecedor"] == "[CPF]"


def test_texto_livre_passa_pelo_mascaramento_de_dados_pessoais():
    linha = linha_contratacao(_contratacao(objetoCompra="Contato: CPF 123.456.789-09, fulano@gmail.com"), "api")
    assert "123.456.789-09" not in linha["objeto_compra"] and "fulano@gmail.com" not in linha["objeto_compra"]


# ----------------------------------------------------------------- o banco


def test_carrega_as_duas_entidades_nas_tabelas(tmp_path, pastas):
    _pagina(pastas, "api", "contratacoes/modalidade-8/pagina_000001.json", [_contratacao()])
    _pagina(pastas, "api", "contratos/cnpjOrgao-1/pagina_000001.json", [_contrato()])
    r = normalizar(_manifesto(tmp_path), "v1", pastas)

    assert r["tabelas"] == {"contratacao": 1, "contrato": 1, "orgao": 1}
    assert _linhas(pastas, "SELECT valor_global, ni_fornecedor FROM contrato") == [{"valor_global": 631959574.8, "ni_fornecedor": "34164319000506"}]
    assert _linhas(pastas, "SELECT razao_social FROM orgao") == [{"razao_social": "BANCO CENTRAL DO BRASIL"}]


def test_mesmo_registro_nas_duas_fontes_vira_uma_linha_com_o_mais_recente(tmp_path, pastas):
    antigo = _contratacao(atualizacao="2026-05-21T11:16:46", objetoCompra="texto antigo")
    novo = _contratacao(atualizacao="2026-07-01T09:00:00", objetoCompra="texto novo")
    _pagina(pastas, "api", "contratacoes/modalidade-8/pagina_000001.json", [antigo])
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", [novo])
    r = normalizar(_manifesto(tmp_path), "v1", pastas)

    (linha,) = _linhas(pastas, "SELECT objeto_compra, fonte FROM contratacao")
    assert linha == {"objeto_compra": "texto novo", "fonte": "api+snapshot"}
    assert r["presentes_em_mais_de_uma_fonte"] == {"contratacao": 1}
    assert r["com_conteudo_diferente_entre_fontes"] == {"contratacao": 1}


def test_empate_de_data_prefere_a_api(tmp_path, pastas):
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", [_contratacao(objetoCompra="do snapshot")])
    _pagina(pastas, "api", "contratacoes/modalidade-8/pagina_000001.json", [_contratacao(objetoCompra="da API")])
    normalizar(_manifesto(tmp_path), "v1", pastas)
    assert _linhas(pastas, "SELECT objeto_compra FROM contratacao")[0]["objeto_compra"] == "da API"


def test_registro_identico_nas_duas_fontes_nao_conta_como_divergente(tmp_path, pastas):
    _pagina(pastas, "api", "contratos/cnpjOrgao-1/pagina_000001.json", [_contrato()])
    _pagina(pastas, "snap", "contratos/pagina_000001.json", [_contrato()])
    r = normalizar(_manifesto(tmp_path), "v1", pastas)
    assert r["tabelas"]["contrato"] == 1
    assert r["presentes_em_mais_de_uma_fonte"] == {"contrato": 1}
    assert r["com_conteudo_diferente_entre_fontes"]["contrato"] == 0


def test_registros_invalidos_sao_rejeitados_e_contados(tmp_path, pastas):
    itens = [_contratacao(), _contratacao(numero="invalido"), {"semNumero": 1}, _contratacao(numero=None)]
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", itens)
    r = normalizar(_manifesto(tmp_path), "v1", pastas)

    assert r["tabelas"]["contratacao"] == 1
    assert r["rejeitados"] == {"contratacao: sem número de controle válido": 3}
    assert len(r["exemplos_rejeitados"]) == 3


def test_relatorio_conta_cpf_mascarado_vinculo_e_periodo(tmp_path, pastas):
    contratos = [
        _contrato(numero="00038166000105-2-000001/2026"),
        _contrato(numero="00038166000105-2-000002/2026", tipoPessoa="PF", niFornecedor="12345678909"),
        _contrato(numero="00038166000105-2-000003/2026", compra="00038166000105-1-999999/2026"),  # compra inexistente
    ]
    _pagina(pastas, "snap", "contratos/pagina_000001.json", contratos)
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", [_contratacao()])
    r = normalizar(_manifesto(tmp_path), "v1", pastas)

    assert r["fornecedores_pessoa_fisica_mascarados"] == 1
    assert r["contratos_com_contratacao_encontrada"] == {"total": 2, "percentual": 66.7}
    assert r["periodo_de_publicacao"] == {"de": "2026-05-21", "ate": "2026-06-23"}


def test_rodar_de_novo_gera_o_mesmo_conteudo_e_nao_deixa_temporario(tmp_path, pastas):
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", [_contratacao(), _contratacao(numero="00038166000105-1-000001/2026")])
    _pagina(pastas, "snap", "contratos/pagina_000001.json", [_contrato()])
    normalizar(_manifesto(tmp_path), "v1", pastas)
    primeiro = _linhas(pastas, "SELECT * FROM contratacao ORDER BY numero_controle_pncp")
    normalizar(_manifesto(tmp_path), "v1", pastas)

    assert _linhas(pastas, "SELECT * FROM contratacao ORDER BY numero_controle_pncp") == primeiro
    assert not list((pastas.dados / "normalized" / "teste").glob("*.tmp"))


def test_ignora_bases_que_nao_sao_estruturadas(tmp_path, pastas):
    _pagina(pastas, "leis", "contratacoes/pagina_000001.json", [_contratacao()])  # base normativa: não entra
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", [_contratacao(numero="00038166000105-1-000002/2026")])
    r = normalizar(_manifesto(tmp_path), "v1", pastas)
    assert r["tabelas"]["contratacao"] == 1


def test_o_banco_gerado_tem_indices_e_chave_primaria(tmp_path, pastas):
    _pagina(pastas, "snap", "contratacoes/pagina_000001.json", [_contratacao()])
    normalizar(_manifesto(tmp_path), "v1", pastas)
    indices = {l["name"] for l in _linhas(pastas, "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'ix_%'")}
    assert {"ix_contratacao_ano", "ix_contrato_assinatura", "ix_contrato_compra"} <= indices
    with pytest.raises(sqlite3.IntegrityError):
        banco = pastas.dados / "normalized" / "teste" / "v1.sqlite"
        c = sqlite3.connect(banco)
        try:
            c.execute("INSERT INTO contratacao (numero_controle_pncp) VALUES ('00038166000105-1-000322/2026')")
        finally:
            c.close()


def test_versao_sem_paginas_da_erro_claro(tmp_path, pastas):
    (pastas.versao("v1") / "snap").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="nenhuma página"):
        normalizar(_manifesto(tmp_path), "v1", pastas)
    with pytest.raises(FileNotFoundError, match="não foi coletada"):
        normalizar(_manifesto(tmp_path), "v9", pastas)
