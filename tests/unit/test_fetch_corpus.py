"""Testes do fetch_corpus. Rodam offline: um servidor HTTP local simula as fontes."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from src.fetch_corpus import (
    ManifestoInvalido,
    Pastas,
    VersaoImutavel,
    carregar_manifesto,
    diff_inventarios,
    fetch,
    hash_do_manifesto,
    inventory,
    nome_de_url,
    proxima_versao,
    update,
    versoes_existentes,
)

PDF_A = b"%PDF-1.4 conteudo A"
PDF_B = b"%PDF-1.4 conteudo B"


class _Estado:
    def __init__(self) -> None:
        self.chamadas: dict[str, int] = {}
        self.conteudo_muda = PDF_A
        self.falhas_422_restantes = 0
        self.limite_429_restantes = 0
        self.retry_after: str | None = None
        self.consultas: list[str] = []


@pytest.fixture()
def servidor():
    estado = _Estado()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silencia o log do servidor
            pass

        def _responder(self, codigo, corpo=b"", tipo="application/pdf"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            url = urlparse(self.path)
            estado.chamadas[url.path] = estado.chamadas.get(url.path, 0) + 1
            estado.consultas.append(self.path)
            if url.path == "/ok.pdf":
                self._responder(200, PDF_A)
            elif url.path == "/muda.pdf":
                self._responder(200, estado.conteudo_muda)
            elif url.path == "/arquivos/1":  # sem extensão: o tipo vem do Content-Type
                self._responder(200, PDF_B)
            elif url.path == "/arquivos/2":  # o PNCP entrega PDF como octet-stream
                self._responder(200, PDF_A, "application/octet-stream")
            elif url.path == "/arquivos/3":
                self._responder(200, b"<!DOCTYPE html><html><body>oi</body></html>", "application/octet-stream")
            elif url.path == "/arquivos/4":
                self._responder(200, b"\x00\x01\x02 dados sem formato conhecido", "application/octet-stream")
            elif url.path == "/nao-existe.pdf":
                self._responder(404, b"nao encontrado", "text/plain")
            elif url.path == "/proibido.pdf":
                self._responder(403, b"proibido", "text/plain")
            elif url.path == "/instavel.pdf":
                if estado.falhas_422_restantes > 0:
                    estado.falhas_422_restantes -= 1
                    corpo = b'{"status":"422","message":"Erro ao tentar carregar arquivo xyz"}'
                    self._responder(422, corpo, "application/json")
                else:
                    self._responder(200, PDF_A)
            elif url.path == "/limitado.pdf":  # o servidor pede calma (HTTP 429)
                if estado.limite_429_restantes > 0:
                    estado.limite_429_restantes -= 1
                    self.send_response(429)
                    if estado.retry_after:
                        self.send_header("Retry-After", estado.retry_after)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._responder(200, PDF_A)
            elif url.path == "/api/vazio":  # consulta válida sem nenhum registro
                self.send_response(204)
                self.end_headers()
            elif url.path == "/api/compras":  # uma consulta por modalidade; a 3 não tem nenhum registro
                q = parse_qs(url.query)
                modalidade, pagina = int(q["modalidade"][0]), int(q["pagina"][0])
                if modalidade == 3:
                    self.send_response(204)
                    self.end_headers()
                    return
                if modalidade == 1:
                    itens = [{"orgaoEntidade": {"cnpj": "111"}}, {"orgaoEntidade": {"cnpj": "222"}}] if pagina == 1 else [{"orgaoEntidade": {"cnpj": "111"}}]
                    corpo = {"data": itens, "totalPaginas": 2}
                else:
                    corpo = {"data": [{"orgaoEntidade": {"cnpj": "333"}}], "totalPaginas": 1}
                self._responder(200, json.dumps(corpo).encode(), "application/json")
            elif url.path == "/api/contratos-orgao":  # contratos de um órgão; o 222 não tem nenhum
                cnpj = parse_qs(url.query)["cnpjOrgao"][0]
                if cnpj == "222":
                    self.send_response(204)
                    self.end_headers()
                    return
                corpo = {"data": [{"numeroControlePNCP": f"contrato-{cnpj}"}], "totalPaginas": 1}
                self._responder(200, json.dumps(corpo).encode(), "application/json")
            elif url.path == "/api/contratos":
                pagina = int(parse_qs(url.query).get("pagina", ["1"])[0])
                itens = [{"id": pagina * 10 + i} for i in range(2)] if pagina <= 3 else []
                corpo = json.dumps({"data": itens, "totalPaginas": 3}).encode()
                self._responder(200, corpo, "application/json")
            else:
                self._responder(404, b"?", "text/plain")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    estado.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield estado
    httpd.shutdown()


@pytest.fixture()
def pastas(tmp_path) -> Pastas:
    return Pastas(dados=tmp_path / "data", cenario=tmp_path / "scenarios" / "teste", cenario_id="teste")


def _manifesto(tmp_path, bases) -> Path:
    caminho = tmp_path / "corpus_manifest.yaml"
    caminho.write_text(yaml.safe_dump({"scenario_id": "teste", "version": "x", "bases": bases}), encoding="utf-8")
    return caminho


def _base_pdf(servidor, urls, **extra):
    acesso = {"tipo": "http_pdf", "urls": [servidor.base + u for u in urls], **extra}
    return {"base_id": "normas", "natureza": "normativa", "acesso": acesso, "volume_alvo": len(urls)}


def _rodar(manifesto, pastas, versao="v1", **opcoes):
    esperas: list[float] = []
    inv = fetch(manifesto, versao, pastas, dormir=esperas.append, escrever=lambda _: None, **opcoes)
    return inv, esperas


# ----------------------------------------------------------------- manifesto


def test_manifesto_rejeita_natureza_desconhecida(tmp_path):
    caminho = _manifesto(tmp_path, [{"base_id": "a", "natureza": "inventada", "acesso": {"tipo": "http_pdf"}}])
    with pytest.raises(ManifestoInvalido, match="natureza"):
        carregar_manifesto(caminho)


def test_manifesto_rejeita_tipo_de_acesso_desconhecido(tmp_path):
    caminho = _manifesto(tmp_path, [{"base_id": "a", "natureza": "normativa", "acesso": {"tipo": "ftp"}}])
    with pytest.raises(ManifestoInvalido, match="acesso"):
        carregar_manifesto(caminho)


def test_manifesto_rejeita_base_id_repetido(tmp_path):
    base = {"base_id": "a", "natureza": "normativa", "acesso": {"tipo": "http_pdf"}}
    with pytest.raises(ManifestoInvalido, match="repetido"):
        carregar_manifesto(_manifesto(tmp_path, [base, dict(base)]))


def test_hash_do_manifesto_ignora_quebra_de_linha(tmp_path):
    lf = tmp_path / "lf.yaml"
    crlf = tmp_path / "crlf.yaml"
    lf.write_bytes(b"a: 1\nb: 2\n")
    crlf.write_bytes(b"a: 1\r\nb: 2\r\n")
    assert hash_do_manifesto(lf) == hash_do_manifesto(crlf)
    assert hash_do_manifesto(lf).startswith("sha256:")


def test_nome_de_url_e_seguro_e_deterministico():
    url = "https://pncp.gov.br/pncp-api/v1/orgaos/42498600000171/compras/2025/926/arquivos/1"
    nome = nome_de_url(url)
    assert nome == nome_de_url(url)
    assert "/" not in nome and ":" not in nome and "?" not in nome


# ----------------------------------------------------------------- coleta http


def test_baixa_arquivos_e_registra_hash(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf"])])
    inv, _ = _rodar(manifesto, pastas)

    (arquivo,) = inv["arquivos"]
    assert arquivo["sha256"] == hashlib.sha256(PDF_A).hexdigest()
    assert arquivo["bytes"] == len(PDF_A)
    assert (tmp_path / arquivo["path"]).read_bytes() == PDF_A
    assert inv["totais"]["falhas"] == 0


def test_extensao_vem_do_content_type_quando_a_url_nao_tem(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/arquivos/1"])])
    inv, _ = _rodar(manifesto, pastas)

    assert inv["arquivos"][0]["path"].endswith(".pdf")


def test_url_pode_ser_um_objeto_com_metadados(tmp_path, pastas, servidor):
    base = _base_pdf(servidor, [])
    base["acesso"]["urls"] = [
        {"url": servidor.base + "/ok.pdf", "rotulo": "Edital de teste", "doc_type": "edital", "data_publicacao": "2026-01-01"},
        servidor.base + "/muda.pdf",
    ]
    inv, _ = _rodar(_manifesto(tmp_path, [base]), pastas)

    assert inv["totais"]["arquivos"] == 2
    assert {a["origem"].rsplit("/", 1)[-1] for a in inv["arquivos"]} == {"ok.pdf", "muda.pdf"}


def test_extensao_vem_do_conteudo_quando_o_servidor_diz_octet_stream(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/arquivos/2", "/arquivos/3", "/arquivos/4"])])
    inv, _ = _rodar(manifesto, pastas)

    por_fim = {a["origem"].rsplit("/", 1)[-1]: a["path"].rsplit(".", 1)[-1] for a in inv["arquivos"]}
    assert por_fim == {"2": "pdf", "3": "html", "4": "bin"}
    assert inv["por_base"][0]["formatos"] == {"pdf": 1, "html": 1, "bin": 1}


def test_url_que_falha_vira_falha_sem_abortar_a_coleta(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/nao-existe.pdf", "/ok.pdf"])])
    inv, _ = _rodar(manifesto, pastas)

    assert inv["totais"]["arquivos"] == 1
    assert inv["totais"]["falhas"] == 1
    assert "HTTP 404" in inv["falhas"][0]["erro"]
    assert servidor.chamadas["/nao-existe.pdf"] == 1  # 404 é definitivo: não repete


def test_403_nao_e_repetido_nem_contornado(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/proibido.pdf"])])
    inv, esperas = _rodar(manifesto, pastas)

    assert servidor.chamadas["/proibido.pdf"] == 1
    assert "HTTP 403" in inv["falhas"][0]["erro"]
    assert esperas == []


def test_422_intermitente_do_pncp_e_repetido_com_espera_crescente(tmp_path, pastas, servidor):
    servidor.falhas_422_restantes = 2
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/instavel.pdf"])])
    inv, esperas = _rodar(manifesto, pastas)

    assert inv["totais"]["arquivos"] == 1
    assert servidor.chamadas["/instavel.pdf"] == 3
    assert esperas == [2.0, 4.0]


def test_desiste_depois_de_todas_as_tentativas(tmp_path, pastas, servidor):
    servidor.falhas_422_restantes = 99
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/instavel.pdf"])])
    inv, _ = _rodar(manifesto, pastas)

    assert servidor.chamadas["/instavel.pdf"] == 4
    assert "após 4 tentativas" in inv["falhas"][0]["erro"]


def test_respeita_o_rate_limit_do_manifesto(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf", "/muda.pdf"], rate_limit_seg=1.5)])
    _, esperas = _rodar(manifesto, pastas)

    assert esperas == [1.5, 1.5]


# ----------------------------------------------------------------- idempotência e versões


def test_rodar_duas_vezes_nao_rebaixa_o_que_ja_foi_coletado(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf", "/nao-existe.pdf"])])
    _rodar(manifesto, pastas)
    _rodar(manifesto, pastas)

    assert servidor.chamadas["/ok.pdf"] == 1  # já coletado: não baixa de novo
    assert servidor.chamadas["/nao-existe.pdf"] == 2  # falhou: tenta de novo


def test_retoma_uma_coleta_interrompida(tmp_path, pastas, servidor):
    servidor.falhas_422_restantes = 99
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf", "/instavel.pdf"])])
    inv1, _ = _rodar(manifesto, pastas)
    assert inv1["totais"] == {"arquivos": 1, "bytes": len(PDF_A), "falhas": 1}

    servidor.falhas_422_restantes = 0  # o servidor se recupera
    inv2, _ = _rodar(manifesto, pastas)
    assert inv2["totais"]["arquivos"] == 2
    assert inv2["totais"]["falhas"] == 0
    assert servidor.chamadas["/ok.pdf"] == 1


def test_versao_nao_e_reaproveitada_com_outro_manifesto(tmp_path, pastas, servidor):
    primeiro = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf"])])
    _rodar(primeiro, pastas)

    outro = tmp_path / "outro.yaml"
    outro.write_text(
        yaml.safe_dump({"scenario_id": "teste", "bases": [_base_pdf(servidor, ["/muda.pdf"])]}), encoding="utf-8"
    )
    with pytest.raises(VersaoImutavel, match="update"):
        _rodar(outro, pastas)


def test_proxima_versao(tmp_path, pastas, servidor):
    assert proxima_versao(pastas) == "v1"
    _rodar(_manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf"])]), pastas)
    assert versoes_existentes(pastas) == ["v1"]
    assert proxima_versao(pastas) == "v2"


# ----------------------------------------------------------------- arquivos locais


def _base_local(pasta_local, arquivos):
    return {
        "base_id": "decretos",
        "natureza": "normativa",
        "acesso": {
            "tipo": "arquivo_local",
            "arquivos_locais": {"pasta": str(pasta_local), "arquivos": arquivos},
        },
    }


def test_arquivo_local_conferido_pelo_hash(tmp_path, pastas):
    pasta = tmp_path / "manual"
    pasta.mkdir()
    (pasta / "decreto.pdf").write_bytes(PDF_A)
    sha = hashlib.sha256(PDF_A).hexdigest()
    item = {"arquivo": "decreto.pdf", "sha256": sha, "origem": "https://exemplo.gov.br/decreto"}
    inv, _ = _rodar(_manifesto(tmp_path, [_base_local(pasta, [item])]), pastas, pasta_local=pasta)

    (arquivo,) = inv["arquivos"]
    assert arquivo["sha256"] == sha
    assert arquivo["origem"] == "https://exemplo.gov.br/decreto"  # a origem pública, e não o caminho local


def test_arquivo_local_com_hash_diferente_e_rejeitado(tmp_path, pastas):
    pasta = tmp_path / "manual"
    pasta.mkdir()
    (pasta / "decreto.pdf").write_bytes(PDF_A)
    item = {"arquivo": "decreto.pdf", "sha256": "0" * 64}
    inv, _ = _rodar(_manifesto(tmp_path, [_base_local(pasta, [item])]), pastas, pasta_local=pasta)

    assert inv["totais"]["arquivos"] == 0
    assert "sha256 diferente" in inv["falhas"][0]["erro"]


def test_arquivo_local_ausente_vira_falha(tmp_path, pastas):
    pasta = tmp_path / "manual"
    pasta.mkdir()
    inv, _ = _rodar(_manifesto(tmp_path, [_base_local(pasta, [{"arquivo": "sumiu.pdf"}])]), pastas, pasta_local=pasta)

    assert "ausente" in inv["falhas"][0]["erro"]


# ----------------------------------------------------------------- rest_api


def _base_rest(servidor, **extra):
    acesso = {
        "tipo": "rest_api",
        "endpoint": servidor.base + "/api/contratos",
        "params": {"uf": "RJ"},
        "paginacao": {"campo": "pagina", "tamanho": 2},
        "rate_limit_seg": 1.0,
        **extra,
    }
    return {"base_id": "contratos", "natureza": "estruturada", "acesso": acesso}


def test_rest_api_pagina_ate_o_fim(tmp_path, pastas, servidor):
    inv, esperas = _rodar(_manifesto(tmp_path, [_base_rest(servidor)]), pastas)

    assert inv["totais"]["arquivos"] == 3
    assert servidor.chamadas["/api/contratos"] == 3  # para na última página, sem pedir uma quarta
    assert esperas == [1.0, 1.0, 1.0]


def _base_modalidades_e_orgaos(servidor):
    return {
        "base_id": "estruturada",
        "natureza": "estruturada",
        "acesso": {
            "tipo": "rest_api",
            "params": {"dataInicial": "20260101"},
            "paginacao": {"campo": "pagina", "tamanho": 10},
            "endpoints": {
                "compras": {
                    "url": servidor.base + "/api/compras",
                    "params": {"uf": "RJ"},
                    "iterar": {"modalidade": [1, 2, 3]},
                },
                "contratos": {
                    "url": servidor.base + "/api/contratos-orgao",
                    "por_orgao_de": "compras",
                    "parametro_orgao": "cnpjOrgao",
                },
            },
        },
    }


def test_itera_as_modalidades_e_depois_os_orgaos_encontrados(tmp_path, pastas, servidor):
    inv, _ = _rodar(_manifesto(tmp_path, [_base_modalidades_e_orgaos(servidor)]), pastas)

    assert inv["totais"]["falhas"] == 0
    assert servidor.chamadas["/api/compras"] == 4  # modalidade 1 (2 páginas), 2 e 3 (sem registros)
    assert servidor.chamadas["/api/contratos-orgao"] == 3  # os órgãos 111, 222 e 333, cada um uma vez
    base = pastas.raw / "v1" / "estruturada"
    assert sorted(p.name for p in (base / "compras").iterdir()) == ["modalidade-1", "modalidade-2"]  # a 3 não deixa pasta
    assert sorted(p.name for p in (base / "contratos").iterdir()) == ["cnpjOrgao-111", "cnpjOrgao-333"]  # o 222 também não
    assert inv["totais"]["arquivos"] == 5  # 2 + 1 páginas de compras e 2 de contratos
    assert any("uf=RJ" in c for c in servidor.consultas if c.startswith("/api/compras"))


def test_consulta_sem_registros_nao_e_repetida_na_proxima_execucao(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_modalidades_e_orgaos(servidor)])
    _rodar(manifesto, pastas)
    chamadas_antes = dict(servidor.chamadas)
    inv, _ = _rodar(manifesto, pastas)

    assert servidor.chamadas == chamadas_antes  # nada novo: nem as páginas já coletadas, nem as consultas vazias
    assert inv["totais"]["falhas"] == 0 and inv["totais"]["arquivos"] == 5


def test_por_orgao_de_aponta_para_um_endpoint_que_nao_existe(tmp_path, pastas, servidor):
    base = _base_modalidades_e_orgaos(servidor)
    base["acesso"]["endpoints"]["contratos"]["por_orgao_de"] = "inexistente"
    with pytest.raises(ValueError, match="não existe"):
        _rodar(_manifesto(tmp_path, [base]), pastas)


def test_429_respeita_o_retry_after_do_servidor(tmp_path, pastas, servidor):
    servidor.limite_429_restantes = 1
    servidor.retry_after = "7"
    inv, esperas = _rodar(_manifesto(tmp_path, [_base_pdf(servidor, ["/limitado.pdf"])]), pastas)

    assert inv["totais"]["arquivos"] == 1
    assert esperas == [7.0]


def test_429_sem_retry_after_espera_bem_mais_que_nos_outros_erros(tmp_path, pastas, servidor):
    servidor.limite_429_restantes = 2
    servidor.retry_after = None
    inv, esperas = _rodar(_manifesto(tmp_path, [_base_pdf(servidor, ["/limitado.pdf"])]), pastas)

    assert inv["totais"]["arquivos"] == 1
    assert esperas == [30.0, 60.0]  # 15 x a espera base, dobrando; um 503 esperaria só 2 s e 4 s


def test_204_e_uma_consulta_sem_registros_e_nao_uma_falha(tmp_path, pastas, servidor):
    base = _base_rest(servidor)
    base["acesso"]["endpoint"] = servidor.base + "/api/vazio"
    inv, _ = _rodar(_manifesto(tmp_path, [base]), pastas)

    assert inv["totais"] == {"arquivos": 0, "bytes": 0, "falhas": 0}
    assert not list((pastas.raw / "v1" / "contratos").rglob("*.bin"))  # nenhum arquivo vazio fica para trás


def test_rest_api_respeita_max_paginas(tmp_path, pastas, servidor):
    inv, _ = _rodar(_manifesto(tmp_path, [_base_rest(servidor)]), pastas, max_paginas=2)

    assert inv["totais"]["arquivos"] == 2


def test_rest_api_retoma_sem_rebaixar_paginas(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_rest(servidor)])
    _rodar(manifesto, pastas, max_paginas=2)
    _rodar(manifesto, pastas)

    assert servidor.chamadas["/api/contratos"] == 3  # 2 + só a terceira


# ----------------------------------------------------------------- inventário e diff


def test_inventario_tem_os_campos_e_marca_bases_nao_coletadas(tmp_path, pastas, servidor):
    outra = {"base_id": "outra", "natureza": "agregada", "acesso": {"tipo": "rest_api", "endpoint": "http://x"}}
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf", "/nao-existe.pdf"]), outra])
    inv, _ = _rodar(manifesto, pastas, bases=["normas"])

    assert {"scenario_id", "version", "coletado_em", "manifest_hash", "totais", "por_base", "arquivos", "falhas"} <= set(inv)
    normas = next(b for b in inv["por_base"] if b["base_id"] == "normas")
    assert normas["estado"] == "coletada"
    assert normas["divergencia_alvo"] == -1  # alvo 2, coletou 1
    nao = next(b for b in inv["por_base"] if b["base_id"] == "outra")
    assert nao["estado"] == "nao_coletada"
    assert nao["divergencia_alvo"] is None


def test_base_com_coletar_false_nao_faz_requisicao_e_registra_o_motivo(tmp_path, pastas, servidor):
    adiada = _base_rest(servidor)
    adiada["coletar"] = False
    adiada["motivo_nao_coletada"] = "parâmetros da API ainda não validados"
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf"]), adiada])
    inv, esperas = _rodar(manifesto, pastas)

    assert "/api/contratos" not in servidor.chamadas
    contratos = next(b for b in inv["por_base"] if b["base_id"] == "contratos")
    assert contratos["estado"] == "nao_coletada"
    assert contratos["motivo_nao_coletada"] == "parâmetros da API ainda não validados"
    assert "Bases ainda não coletadas" in (pastas.cenario / "CORPUS_INVENTORY.md").read_text(encoding="utf-8")
    assert inv["totais"]["falhas"] == 0


def test_inventario_e_gravado_em_json_e_markdown(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf", "/nao-existe.pdf"])])
    _rodar(manifesto, pastas)

    dados = json.loads((pastas.inventario / "v1.json").read_text(encoding="utf-8"))
    assert dados["version"] == "v1"
    texto = (pastas.cenario / "CORPUS_INVENTORY.md").read_text(encoding="utf-8")
    assert "## Falhas" in texto and "HTTP 404" in texto
    assert "ok.pdf" in texto


def test_comando_inventory_regera_sem_baixar(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf"])])
    _rodar(manifesto, pastas)
    (pastas.inventario / "v1.json").unlink()

    inv = inventory(manifesto, "v1", pastas)
    assert inv["totais"]["arquivos"] == 1
    assert (pastas.inventario / "v1.json").exists()
    assert servidor.chamadas["/ok.pdf"] == 1


def test_update_cria_v2_sem_tocar_na_v1_e_gera_o_diff(tmp_path, pastas, servidor):
    manifesto = _manifesto(tmp_path, [_base_pdf(servidor, ["/ok.pdf", "/muda.pdf"])])
    _rodar(manifesto, pastas)
    antes = (tmp_path / [a for a in json.loads((pastas.inventario / "v1.json").read_text(encoding="utf-8"))["arquivos"]
                         if a["origem"].endswith("/muda.pdf")][0]["path"]).read_bytes()

    servidor.conteudo_muda = PDF_B
    inv2, caminho_diff = update(manifesto, pastas, dormir=lambda _: None, escrever=lambda _: None)

    assert inv2["version"] == "v2"
    assert versoes_existentes(pastas) == ["v1", "v2"]
    texto = caminho_diff.read_text(encoding="utf-8")
    assert "Mudaram de conteúdo (hash diferente) (1)" in texto
    assert "Permaneceram idênticos (1)" in texto
    v1 = json.loads((pastas.inventario / "v1.json").read_text(encoding="utf-8"))
    muda_v1 = [a for a in v1["arquivos"] if a["origem"].endswith("/muda.pdf")][0]
    assert (tmp_path / muda_v1["path"]).read_bytes() == antes == PDF_A  # a v1 continua com o conteúdo antigo


def test_diff_entrou_saiu_mudou_igual():
    def inv(**arquivos):
        return {"arquivos": [{"origem": o, "sha256": s} for o, s in arquivos.items()]}

    diff = diff_inventarios(inv(a="1", b="2", c="3"), inv(b="2", c="9", d="4"))
    assert diff == {"entrou": ["d"], "saiu": ["a"], "mudou": ["c"], "igual": ["b"]}
