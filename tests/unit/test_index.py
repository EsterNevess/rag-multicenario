"""Testes do índice: embeddings (HTTP), cache em disco, índice FAISS + FTS5 e carregamento."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import faiss
import numpy as np
import pytest

from config import Settings
from src.index import (
    INSTRUCAO_CONSULTA_QWEN3,
    CacheDeEmbeddings,
    EmbedderCompativelComOpenAI,
    EmbeddingIncompativel,
    EmbeddingIndisponivel,
    IndiceIncompativel,
    carregar_indice,
    construir_indice,
    criar_embedder,
    vetorizar_com_cache,
)

DIM = 16


class EmbedderFalso:
    """Vetores determinísticos a partir do texto, sem rede. Conta o que foi calculado."""

    def __init__(self, identity: str = "fake:modelo-a:16", falhar_na_chamada: int | None = None):
        self.identity = identity
        self.dimensions = DIM
        self.textos_calculados: list[str] = []
        self.chamadas = 0
        self.falhar_na_chamada = falhar_na_chamada

    def _vetor(self, texto: str) -> np.ndarray:
        semente = int(hashlib.sha256(texto.encode()).hexdigest()[:12], 16)
        v = np.random.default_rng(semente).normal(size=DIM).astype(np.float32)
        return v / np.linalg.norm(v)

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        self.chamadas += 1
        if self.falhar_na_chamada == self.chamadas:
            raise EmbeddingIndisponivel("servidor caiu")
        self.textos_calculados.extend(textos)
        return np.array([self._vetor(t) for t in textos])

    def embed_consulta(self, textos: list[str]) -> np.ndarray:
        return np.array([self._vetor(t) for t in textos])


def _chunk(n: int, texto: str | None = None) -> dict:
    return {
        "texto": texto or f"Lei X - Art. {n}\nO artigo {n} trata de licitação e contratação direta número {n}.",
        "metadata": {
            "source_file": "lei.html", "doc_type": "lei", "chunk_id": f"base:lei:art{n}:p1",
            "sensitivity": "publico", "base_id": "base", "artigo": str(n),
        },
    }


def _jsonl(tmp_path, chunks, nome="trechos.jsonl"):
    caminho = tmp_path / nome
    caminho.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    return caminho


def _construir(tmp_path, chunks, embedder=None, saida=None, **kw):
    embedder = embedder or EmbedderFalso()
    cache = CacheDeEmbeddings(tmp_path / "cache.sqlite", embedder.identity)
    try:
        resultado = construir_indice(
            _jsonl(tmp_path, chunks), saida or tmp_path / "indice", embedder, cache,
            escrever=lambda _: None, **kw,
        )
    finally:
        cache.fechar()
    return resultado, embedder


# ----------------------------------------------------------------- servidor HTTP falso


class _Servidor:
    def __init__(self) -> None:
        self.dimensao = DIM
        self.status_antes_de_ok: list[int] = []
        self.pedidos: list[dict] = []
        self.cabecalhos: list[dict] = []
        self.embaralhar = True


@pytest.fixture()
def servidor():
    estado = _Servidor()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            estado.pedidos.append(corpo)
            estado.cabecalhos.append(dict(self.headers))
            if estado.status_antes_de_ok:
                codigo = estado.status_antes_de_ok.pop(0)
                self.send_response(codigo)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            if self.path.endswith("/modelo-inexistente/embeddings"):
                pass
            itens = []
            for i, texto in enumerate(corpo["input"]):
                v = np.random.default_rng(len(texto) + 7).normal(size=estado.dimensao) * 3.0  # não normalizado
                itens.append({"index": i, "embedding": v.tolist()})
            if estado.embaralhar:
                itens.reverse()
            dados = json.dumps({"data": itens}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    estado.url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    yield estado
    httpd.shutdown()


def _http(servidor, provider="ollama", model="qwen3-embedding:0.6b", api_key=None, **kw):
    return EmbedderCompativelComOpenAI(provider, servidor.url, model, DIM, api_key=api_key, dormir=lambda _: None, **kw)


# ----------------------------------------------------------------- embedder HTTP


def test_vetores_normalizados_e_na_ordem_dos_textos(servidor):
    textos = ["a", "bb", "ccc"]
    vetores = _http(servidor).embed_documentos(textos)
    assert vetores.shape == (3, DIM)
    assert np.allclose(np.linalg.norm(vetores, axis=1), 1.0, atol=1e-5)
    # o servidor devolveu a lista invertida; o embedder reordena pelo campo "index"
    esperado = np.random.default_rng(1 + 7).normal(size=DIM)
    assert np.allclose(vetores[0], esperado / np.linalg.norm(esperado), atol=1e-5)


def test_dimensao_errada_e_rejeitada(servidor):
    servidor.dimensao = 8
    with pytest.raises(EmbeddingIncompativel, match="esperado"):
        _http(servidor).embed_documentos(["a"])


def test_servidor_fora_do_ar_da_uma_mensagem_que_ensina_o_que_fazer():
    embedder = EmbedderCompativelComOpenAI("ollama", "http://127.0.0.1:9/v1", "qwen3-embedding:0.6b", DIM)
    with pytest.raises(EmbeddingIndisponivel, match="ollama pull qwen3-embedding:0.6b"):
        embedder.embed_documentos(["a"])


def test_falha_passageira_e_repetida_com_espera_crescente(servidor):
    servidor.status_antes_de_ok = [503, 429]
    esperas: list[float] = []
    embedder = EmbedderCompativelComOpenAI("ollama", servidor.url, "m", DIM, dormir=esperas.append)
    assert embedder.embed_documentos(["a"]).shape == (1, DIM)
    assert esperas == [2.0, 4.0]


def test_desiste_depois_das_tentativas(servidor):
    servidor.status_antes_de_ok = [503, 503, 503]
    with pytest.raises(EmbeddingIndisponivel, match="3 tentativas"):
        _http(servidor).embed_documentos(["a"])


def test_modelo_ausente_404_aponta_o_comando(servidor):
    servidor.status_antes_de_ok = [404]
    with pytest.raises(EmbeddingIndisponivel, match="ollama pull"):
        _http(servidor).embed_documentos(["a"])


def test_acesso_recusado_nao_e_repetido(servidor):
    servidor.status_antes_de_ok = [401]
    with pytest.raises(EmbeddingIndisponivel, match="acesso recusado"):
        _http(servidor, provider="openai", model="text-embedding-3-small", api_key="x").embed_documentos(["a"])
    assert len(servidor.pedidos) == 1


def test_openai_envia_dimensions_e_chave_ollama_nao(servidor):
    _http(servidor, provider="openai", model="text-embedding-3-small", api_key="chave-de-teste").embed_documentos(["a"])
    assert servidor.pedidos[-1]["dimensions"] == DIM
    assert servidor.cabecalhos[-1]["Authorization"] == "Bearer chave-de-teste"
    _http(servidor).embed_documentos(["a"])
    assert "dimensions" not in servidor.pedidos[-1]
    assert "Authorization" not in servidor.cabecalhos[-1]


def test_qwen3_usa_instrucao_so_na_consulta(servidor):
    embedder = _http(servidor)
    embedder.embed_documentos(["texto do documento"])
    assert servidor.pedidos[-1]["input"] == ["texto do documento"]
    embedder.embed_consulta(["minha pergunta"])
    assert servidor.pedidos[-1]["input"] == [INSTRUCAO_CONSULTA_QWEN3 + "minha pergunta"]


def test_modelo_sem_instrucao_nao_recebe_prefixo(servidor):
    _http(servidor, provider="openai", model="text-embedding-3-small", api_key="x").embed_consulta(["pergunta"])
    assert servidor.pedidos[-1]["input"] == ["pergunta"]


def test_criar_embedder_usa_a_configuracao():
    padrao = criar_embedder(Settings(_env_file=None))
    assert padrao.identity == "ollama:qwen3-embedding:0.6b:1024"
    with pytest.raises(EmbeddingIndisponivel, match="OPENAI_API_KEY"):
        # openai_api_key=None: o teste não pode depender de uma chave que exista no Windows de quem o roda.
        criar_embedder(
            Settings(_env_file=None, embedding_provider="openai", embedding_model="text-embedding-3-small", openai_api_key=None)
        )


# ----------------------------------------------------------------- cache


def test_cache_evita_recalcular(tmp_path):
    embedder = EmbedderFalso()
    cache = CacheDeEmbeddings(tmp_path / "c.sqlite", embedder.identity)
    textos = [f"texto {i}" for i in range(10)]
    v1, calculados1 = vetorizar_com_cache(embedder, cache, textos, tamanho_lote=4, escrever=lambda _: None)
    v2, calculados2 = vetorizar_com_cache(embedder, cache, textos, tamanho_lote=4, escrever=lambda _: None)
    assert calculados1 == 10 and calculados2 == 0
    assert np.allclose(v1, v2)
    assert embedder.chamadas == 3  # 4 + 4 + 2


def test_cache_nao_mistura_modelos_diferentes(tmp_path):
    caminho = tmp_path / "c.sqlite"
    a, b = EmbedderFalso("fake:modelo-a:16"), EmbedderFalso("fake:modelo-b:16")
    vetorizar_com_cache(a, CacheDeEmbeddings(caminho, a.identity), ["x"], tamanho_lote=4, escrever=lambda _: None)
    _, calculados = vetorizar_com_cache(b, CacheDeEmbeddings(caminho, b.identity), ["x"], tamanho_lote=4, escrever=lambda _: None)
    assert calculados == 1  # o modelo B não reaproveita o vetor do modelo A


def test_pausa_entre_lotes_mas_nao_depois_do_ultimo(tmp_path):
    embedder = EmbedderFalso()
    cache = CacheDeEmbeddings(tmp_path / "c.sqlite", embedder.identity)
    pausas: list[float] = []
    vetorizar_com_cache(embedder, cache, [f"t{i}" for i in range(9)], tamanho_lote=4, pausa=10.0, dormir=pausas.append, escrever=lambda _: None)
    assert pausas == [10.0, 10.0]  # 3 lotes, 2 pausas


def test_queda_no_meio_retoma_so_o_que_falta(tmp_path):
    textos = [f"texto {i}" for i in range(10)]
    cache = CacheDeEmbeddings(tmp_path / "c.sqlite", "fake:modelo-a:16")
    quebrado = EmbedderFalso(falhar_na_chamada=2)
    with pytest.raises(EmbeddingIndisponivel):
        vetorizar_com_cache(quebrado, cache, textos, tamanho_lote=4, escrever=lambda _: None)
    assert len(quebrado.textos_calculados) == 4  # o primeiro lote foi gravado

    recuperado = EmbedderFalso()
    _, calculados = vetorizar_com_cache(recuperado, cache, textos, tamanho_lote=4, escrever=lambda _: None)
    assert calculados == 6 and recuperado.textos_calculados == textos[4:]


# ----------------------------------------------------------------- índice


def test_constroi_indice_vetorial_e_de_palavras(tmp_path):
    chunks = [_chunk(n) for n in range(1, 6)]
    resultado, embedder = _construir(tmp_path, chunks)
    pasta = tmp_path / "indice"

    assert resultado["total_vetores"] == 5 and resultado["reaproveitado"] is False
    assert {p.name for p in pasta.iterdir()} == {"chunks.faiss", "chunk_ids.json", "fts.sqlite", "estado_indexacao.json"}
    carregado = carregar_indice(pasta, embedder.identity)
    assert carregado.chunk_ids == [c["metadata"]["chunk_id"] for c in chunks]
    assert carregado.estado["identity"] == "fake:modelo-a:16" and carregado.estado["dimensao"] == DIM

    # cada trecho é o vizinho mais próximo de si mesmo, com similaridade 1
    consulta = embedder.embed_consulta([chunks[2]["texto"]])
    notas, posicoes = carregado.indice.search(consulta, 1)
    assert carregado.chunk_ids[posicoes[0][0]] == chunks[2]["metadata"]["chunk_id"]
    assert notas[0][0] == pytest.approx(1.0, abs=1e-5)


def test_busca_de_palavras_ignora_acento_e_ordena_por_bm25(tmp_path):
    chunks = [
        _chunk(1, "Texto comum sobre obras e serviços."),
        _chunk(2, "A dispensa de licitação é a contratação direta."),
        _chunk(3, "Licitação licitação licitação e mais licitação."),
    ]
    _construir(tmp_path, chunks)
    conexao = sqlite3.connect(tmp_path / "indice" / "fts.sqlite")
    linhas = conexao.execute(
        "SELECT chunk_id FROM trechos WHERE trechos MATCH 'licitacao' ORDER BY bm25(trechos)"
    ).fetchall()
    assert [l[0] for l in linhas] == ["base:lei:art3:p1", "base:lei:art2:p1"]  # sem acento acha "licitação"; o mais repetido primeiro
    meta = conexao.execute("SELECT metadata FROM metadados WHERE chunk_id = 'base:lei:art2:p1'").fetchone()
    assert json.loads(meta[0])["artigo"] == "2"


def test_nada_mudou_reaproveita_sem_chamar_o_modelo(tmp_path):
    chunks = [_chunk(n) for n in range(1, 4)]
    _, primeiro = _construir(tmp_path, chunks)
    segundo_embedder = EmbedderFalso()
    resultado, _ = _construir(tmp_path, chunks, embedder=segundo_embedder)
    assert resultado["reaproveitado"] is True
    assert segundo_embedder.chamadas == 0


def test_fonte_alterada_reconstroi_calculando_so_os_novos(tmp_path):
    _construir(tmp_path, [_chunk(n) for n in range(1, 4)])
    novo = EmbedderFalso()
    resultado, _ = _construir(tmp_path, [_chunk(n) for n in range(1, 6)], embedder=novo)
    assert resultado["reaproveitado"] is False and resultado["total_vetores"] == 5
    assert resultado["calculados_agora"] == 2 and len(novo.textos_calculados) == 2


def test_modelo_diferente_nao_carrega_o_indice_antigo(tmp_path):
    _construir(tmp_path, [_chunk(1)])
    with pytest.raises(IndiceIncompativel, match="não podem ser misturados"):
        carregar_indice(tmp_path / "indice", "ollama:qwen3-embedding:0.6b:1024")


def test_mudar_o_modelo_reconstrói_em_vez_de_reaproveitar(tmp_path):
    chunks = [_chunk(n) for n in range(1, 3)]
    _construir(tmp_path, chunks, embedder=EmbedderFalso("fake:modelo-a:16"))
    outro = EmbedderFalso("fake:modelo-b:16")
    resultado, _ = _construir(tmp_path, chunks, embedder=outro)
    assert resultado["reaproveitado"] is False and resultado["identity"] == "fake:modelo-b:16"
    assert len(outro.textos_calculados) == 2


def test_indice_corrompido_e_detectado(tmp_path):
    _construir(tmp_path, [_chunk(n) for n in range(1, 4)])
    ids = tmp_path / "indice" / "chunk_ids.json"
    ids.write_text(json.dumps(["so-um"]), encoding="utf-8")
    with pytest.raises(IndiceIncompativel, match="corrompido"):
        carregar_indice(tmp_path / "indice")


def test_carregar_sem_indice_explica_o_que_fazer(tmp_path):
    with pytest.raises(FileNotFoundError, match="python -m src.index build"):
        carregar_indice(tmp_path / "nao-existe")


def test_chunk_id_repetido_e_arquivo_vazio_sao_rejeitados(tmp_path):
    with pytest.raises(ValueError, match="repetido"):
        _construir(tmp_path, [_chunk(1), _chunk(1)])
    with pytest.raises(ValueError, match="não tem trechos"):
        _construir(tmp_path, [], saida=tmp_path / "outro")


def test_falha_no_meio_nao_deixa_indice_pela_metade(tmp_path):
    with pytest.raises(EmbeddingIndisponivel):
        _construir(tmp_path, [_chunk(n) for n in range(1, 8)], embedder=EmbedderFalso(falhar_na_chamada=2), tamanho_lote=3)
    assert not (tmp_path / "indice").exists()
    assert not (tmp_path / "indice.tmp").exists()


def test_indice_novo_nao_deixa_pasta_temporaria(tmp_path):
    _construir(tmp_path, [_chunk(1)])
    assert not (tmp_path / "indice.tmp").exists()


def test_um_indice_por_base_sao_independentes(tmp_path):
    a, _ = _construir(tmp_path, [_chunk(n) for n in range(1, 4)], saida=tmp_path / "base_a")
    b, _ = _construir(tmp_path, [_chunk(n) for n in range(1, 8)], saida=tmp_path / "base_b")
    assert carregar_indice(tmp_path / "base_a").indice.ntotal == 3
    assert carregar_indice(tmp_path / "base_b").indice.ntotal == 7
