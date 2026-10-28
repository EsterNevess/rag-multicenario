"""Testes da importação de um snapshot SQLite (payloads da API do PNCP já coletados)."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from pathlib import Path

import pytest
import yaml

from src.fetch_corpus import ManifestoInvalido, Pastas, fetch


@pytest.fixture()
def pastas(tmp_path) -> Pastas:
    return Pastas(dados=tmp_path / "data", cenario=tmp_path / "scenarios" / "teste", cenario_id="teste")


def _banco(caminho: Path, contratacoes: int = 5, contratos: int = 3, embaralhar: bool = False) -> Path:
    linhas = [("contratacao", f"c{i:03d}", {"numeroControlePNCP": f"compra-{i}", "orgaoEntidade": {"cnpj": f"{i}"}}) for i in range(contratacoes)]
    linhas += [("contrato", f"k{i:03d}", {"numeroControlePNCP": f"contrato-{i}", "valorGlobal": i * 10.5}) for i in range(contratos)]
    if embaralhar:
        random.Random(7).shuffle(linhas)
    conexao = sqlite3.connect(caminho)
    conexao.execute("CREATE TABLE raw_asset (id TEXT PRIMARY KEY, resource_type TEXT, external_id TEXT, payload TEXT, payload_hash TEXT)")
    for n, (tipo, externo, payload) in enumerate(linhas):
        texto = json.dumps(payload, ensure_ascii=False)
        conexao.execute(
            "INSERT INTO raw_asset VALUES (?, ?, ?, ?, ?)",
            (f"id-{n}", tipo, externo, texto, hashlib.sha256(texto.encode()).hexdigest()),
        )
    conexao.commit()
    conexao.close()
    return caminho


def _manifesto(tmp_path, **acesso_extra) -> Path:
    acesso = {
        "tipo": "snapshot_sqlite",
        "arquivo": "nao/usado.db",
        "tipos": {"contratacao": "contratacoes", "contrato": "contratos"},
        "tamanho_pagina": 2,
        **acesso_extra,
    }
    caminho = tmp_path / "corpus_manifest.yaml"
    caminho.write_text(
        yaml.safe_dump({"scenario_id": "teste", "bases": [{"base_id": "snap", "natureza": "estruturada", "acesso": acesso}]}),
        encoding="utf-8",
    )
    return caminho


def _importar(manifesto, pastas, banco, versao="v1"):
    return fetch(manifesto, versao, pastas, dormir=lambda _: None, escrever=lambda _: None, snapshot_db=banco)


def _sha(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def test_importa_em_paginas_no_formato_da_api(tmp_path, pastas):
    inv = _importar(_manifesto(tmp_path), pastas, _banco(tmp_path / "snap.db"))

    assert inv["totais"] == {"arquivos": 5, "bytes": inv["totais"]["bytes"], "falhas": 0}  # 3 páginas + 2 páginas
    base = pastas.raw / "v1" / "snap"
    assert sorted(p.name for p in (base / "contratacoes").iterdir()) == ["pagina_000001.json", "pagina_000002.json", "pagina_000003.json"]
    primeira = json.loads((base / "contratacoes" / "pagina_000001.json").read_text(encoding="utf-8"))
    assert primeira["fonte"] == "snapshot" and primeira["tipo"] == "contratacao"
    assert [i["numeroControlePNCP"] for i in primeira["data"]] == ["compra-0", "compra-1"]  # ordem do external_id
    assert inv["snapshot"]["tipos"]["contratacao"]["linhas"] == 5
    assert inv["snapshot"]["tipos"]["contrato"]["paginas"] == 2


def test_ordem_das_paginas_nao_depende_da_ordem_das_linhas_no_banco(tmp_path, pastas):
    a = _importar(_manifesto(tmp_path), pastas, _banco(tmp_path / "a.db"))
    outras = Pastas(dados=tmp_path / "data2", cenario=tmp_path / "sc2", cenario_id="teste")
    b = _importar(_manifesto(tmp_path), outras, _banco(tmp_path / "b.db", embaralhar=True))

    assert {x["sha256"] for x in a["arquivos"]} == {x["sha256"] for x in b["arquivos"]}
    assert a["snapshot"]["tipos"] == {k: v for k, v in b["snapshot"]["tipos"].items()}


def test_importar_de_novo_nao_reescreve_nem_duplica_registros(tmp_path, pastas):
    manifesto, banco = _manifesto(tmp_path), _banco(tmp_path / "snap.db")
    _importar(manifesto, pastas, banco)
    registro = pastas.raw / "v1" / "_registro.jsonl"
    linhas_antes = registro.read_text(encoding="utf-8").count("\n")
    pagina = pastas.raw / "v1" / "snap" / "contratos" / "pagina_000001.json"
    mtime_antes = pagina.stat().st_mtime_ns

    inv = _importar(manifesto, pastas, banco)

    assert registro.read_text(encoding="utf-8").count("\n") == linhas_antes
    assert pagina.stat().st_mtime_ns == mtime_antes
    assert inv["totais"]["arquivos"] == 5


def test_o_banco_de_origem_nao_e_alterado(tmp_path, pastas):
    banco = _banco(tmp_path / "snap.db")
    antes = _sha(banco)
    _importar(_manifesto(tmp_path), pastas, banco)
    assert _sha(banco) == antes


def test_banco_ausente_vira_falha_sem_derrubar(tmp_path, pastas):
    inv = _importar(_manifesto(tmp_path), pastas, tmp_path / "nao_existe.db")

    assert inv["totais"]["arquivos"] == 0 and inv["totais"]["falhas"] == 1
    assert "ausente" in inv["falhas"][0]["erro"]


def test_impressao_digital_errada_nao_grava_nada(tmp_path, pastas):
    manifesto = _manifesto(tmp_path, impressao_digital={"contratacao": "0" * 64})
    inv = _importar(manifesto, pastas, _banco(tmp_path / "snap.db"))

    assert "difere" in inv["falhas"][0]["erro"]
    assert not (pastas.raw / "v1" / "snap" / "contratacoes").exists()  # a conferência vem antes de gravar
    assert inv["snapshot"]["tipos"].keys() == {"contrato"}  # o outro tipo, sem divergência, foi importado


def test_impressao_digital_correta_e_aceita(tmp_path, pastas):
    banco = _banco(tmp_path / "snap.db")
    primeira = _importar(_manifesto(tmp_path), pastas, banco)
    digitais = {t: d["impressao_digital"] for t, d in primeira["snapshot"]["tipos"].items()}

    outras = Pastas(dados=tmp_path / "data2", cenario=tmp_path / "sc2", cenario_id="teste")
    inv = _importar(_manifesto(tmp_path, impressao_digital=digitais), outras, banco)
    assert inv["totais"]["falhas"] == 0


@pytest.mark.parametrize("campo", ["tabela", "coluna_tipo", "coluna_payload", "coluna_hash", "coluna_ordem"])
def test_nome_de_tabela_ou_coluna_com_sql_solto_e_rejeitado(tmp_path, pastas, campo):
    manifesto = _manifesto(tmp_path, **{campo: "raw_asset; DROP TABLE raw_asset"})
    with pytest.raises(ManifestoInvalido, match="identificador SQL inválido"):
        _importar(manifesto, pastas, _banco(tmp_path / "snap.db"))


def test_caminho_da_linha_de_comando_vale_mais_que_o_do_manifesto(tmp_path, pastas):
    # O manifesto aponta para um arquivo que não existe; o banco passado na chamada é o que vale.
    inv = _importar(_manifesto(tmp_path, arquivo="caminho/que/nao/existe.db"), pastas, _banco(tmp_path / "outro.db"))
    assert inv["totais"]["falhas"] == 0 and inv["totais"]["arquivos"] == 5


def test_inventario_em_markdown_mostra_o_snapshot(tmp_path, pastas):
    _importar(_manifesto(tmp_path), pastas, _banco(tmp_path / "snap.db"))
    texto = (pastas.cenario / "CORPUS_INVENTORY.md").read_text(encoding="utf-8")
    assert "## Snapshot importado" in texto and "contratacao: 5 registros" in texto
