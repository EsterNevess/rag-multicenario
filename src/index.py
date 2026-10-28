"""Índices de uma base: vetorial (FAISS) e de palavras (SQLite FTS5, que dá o BM25).

    python -m src.index build  --scenario rj [--versao v2] [--base editais_normativo]
    python -m src.index status --scenario rj [--base editais_normativo]

Lê os trechos gerados pelo `ingest` e grava, para cada base, uma pasta própria em
data/index/<cenário>/<base>/ com:

    chunks.faiss            vetores normalizados (a busca por produto interno é a similaridade do cosseno)
    chunk_ids.json          o chunk_id de cada vetor, na mesma ordem
    fts.sqlite              índice de palavras (BM25) e os metadados de cada trecho
    estado_indexacao.json   de onde veio e com qual modelo foi feito

Decisões:
- Um índice por base. Em um índice único, uma base grande afoga uma pequena: no projeto em equipe,
  os artigos da lei sumiram entre dezenas de milhares de contratos.
- O índice guarda o provedor, o modelo e a dimensão dos embeddings. Vetores de modelos diferentes
  não se misturam, e carregar um índice incompatível é um erro, não um resultado estranho.
- Cache de embeddings em disco: um texto já calculado nunca é calculado de novo, e uma queda no meio
  da indexação retoma de onde parou. "Carregar, nunca reindexar."
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import faiss
import numpy as np
import requests

from config import RAIZ, Settings, get_settings
from src.fetch_corpus import pastas_do_cenario, versoes_existentes
from src.fsutil import com_repeticao

ESQUEMA = "faiss_flat_ip_v1"
INSTRUCAO_CONSULTA_QWEN3 = (
    "Instruct: Given a question about Brazilian public procurement, "
    "retrieve the legal and contract passages that answer it\nQuery: "
)

Dormir = Callable[[float], None]


class EmbeddingIndisponivel(RuntimeError):
    """O servidor de embeddings não respondeu (por exemplo, o Ollama não está rodando)."""


class EmbeddingIncompativel(ValueError):
    """O modelo devolveu vetores com forma diferente da configurada."""


class IndiceIncompativel(ValueError):
    """O índice em disco foi feito com outro provedor, modelo ou dimensão, ou está corrompido."""


# --------------------------------------------------------------------------- embeddings


class Embedder(Protocol):
    identity: str
    dimensions: int

    def embed_documentos(self, textos: list[str]) -> np.ndarray: ...

    def embed_consulta(self, textos: list[str]) -> np.ndarray: ...


def _normalizar(vetores: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(vetores, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return (vetores / normas).astype(np.float32)


class EmbedderCompativelComOpenAI:
    """Fala com qualquer servidor que implemente POST /embeddings no formato da OpenAI (Ollama, OpenAI)."""

    def __init__(
        self,
        provider: str,
        base_url: str,
        model: str,
        dimensions: int,
        *,
        api_key: str | None = None,
        sessao: requests.Session | None = None,
        dormir: Dormir = time.sleep,
        tentativas: int = 3,
        timeout: float = 600.0,
    ):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.identity = f"{provider}:{model}:{dimensions}"
        self.api_key = api_key
        self.sessao = sessao or requests.Session()
        self.dormir = dormir
        self.tentativas = tentativas
        self.timeout = timeout  # em CPU, um lote de trechos longos pode levar minutos
        # O Qwen3-Embedding trata a consulta com uma instrução; o documento vai sem nada.
        self.prefixo_consulta = INSTRUCAO_CONSULTA_QWEN3 if "qwen3-embedding" in model.lower() else ""

    def _pedir(self, textos: list[str]) -> np.ndarray:
        corpo: dict[str, Any] = {"model": self.model, "input": textos}
        if self.provider == "openai":
            corpo["dimensions"] = self.dimensions  # só a OpenAI aceita reduzir a dimensão
        cabecalhos = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        ultimo = "sem resposta"
        for tentativa in range(1, self.tentativas + 1):
            try:
                resposta = self.sessao.post(f"{self.base_url}/embeddings", json=corpo, headers=cabecalhos, timeout=self.timeout)
            except requests.ConnectionError as erro:
                raise EmbeddingIndisponivel(
                    f"O servidor de embeddings não respondeu em {self.base_url}. "
                    + (
                        "Confira se o Ollama está instalado e rodando (https://ollama.com) e se o modelo foi baixado: "
                        f"ollama pull {self.model}"
                        if self.provider == "ollama"
                        else "Confira a conexão e a chave de API."
                    )
                ) from erro
            except requests.Timeout:
                ultimo = "tempo esgotado"
            else:
                if resposta.ok:
                    dados = sorted(resposta.json()["data"], key=lambda item: item.get("index", 0))
                    vetores = np.array([item["embedding"] for item in dados], dtype=np.float32)
                    if vetores.ndim != 2 or vetores.shape != (len(textos), self.dimensions):
                        raise EmbeddingIncompativel(
                            f"o modelo {self.model} devolveu vetores de forma {vetores.shape}, "
                            f"esperado ({len(textos)}, {self.dimensions})"
                        )
                    return _normalizar(vetores)
                if resposta.status_code in (401, 403):
                    raise EmbeddingIndisponivel(f"acesso recusado pelo servidor de embeddings (HTTP {resposta.status_code})")
                if resposta.status_code == 404:
                    raise EmbeddingIndisponivel(
                        f"o servidor não conhece o modelo {self.model} (HTTP 404)."
                        + (f" Rode: ollama pull {self.model}" if self.provider == "ollama" else "")
                    )
                ultimo = f"HTTP {resposta.status_code}"
                if resposta.status_code not in (429, 500, 502, 503, 504):
                    raise EmbeddingIndisponivel(f"erro do servidor de embeddings: {ultimo}: {resposta.text[:200]}")
            if tentativa < self.tentativas:
                self.dormir(2.0 * 2 ** (tentativa - 1))
        raise EmbeddingIndisponivel(f"o servidor de embeddings falhou após {self.tentativas} tentativas ({ultimo})")

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        return self._pedir(textos)

    def embed_consulta(self, textos: list[str]) -> np.ndarray:
        return self._pedir([self.prefixo_consulta + t for t in textos])


def criar_embedder(config: Settings | None = None, **opcoes: Any) -> EmbedderCompativelComOpenAI:
    config = config or get_settings()
    if config.embedding_provider == "openai":
        chave = config.openai_api_key.get_secret_value() if config.openai_api_key else None
        if not chave:
            raise EmbeddingIndisponivel("OPENAI_API_KEY não está configurada no .env")
        return EmbedderCompativelComOpenAI(
            "openai", "https://api.openai.com/v1", config.embedding_model, config.embedding_dimensions, api_key=chave, **opcoes
        )
    return EmbedderCompativelComOpenAI(
        "ollama", config.ollama_url, config.embedding_model, config.embedding_dimensions, **opcoes
    )


# --------------------------------------------------------------------------- cache em disco


class CacheDeEmbeddings:
    """SQLite com um vetor por (provedor, modelo, dimensão, texto). Gravado a cada lote."""

    def __init__(self, caminho: Path, identity: str):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.conexao = sqlite3.connect(caminho)
        self.conexao.execute("CREATE TABLE IF NOT EXISTS emb (chave TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL)")
        self.conexao.commit()

    def _chave(self, texto: str) -> str:
        return hashlib.sha256(f"{self.identity}\0{texto}".encode("utf-8")).hexdigest()

    def obter(self, textos: list[str]) -> dict[int, np.ndarray]:
        achados: dict[int, np.ndarray] = {}
        for i, texto in enumerate(textos):
            linha = self.conexao.execute("SELECT vec FROM emb WHERE chave = ?", (self._chave(texto),)).fetchone()
            if linha:
                achados[i] = np.frombuffer(linha[0], dtype=np.float32)
        return achados

    def guardar(self, textos: list[str], vetores: np.ndarray) -> None:
        self.conexao.executemany(
            "INSERT OR REPLACE INTO emb (chave, dim, vec) VALUES (?, ?, ?)",
            [(self._chave(t), int(v.shape[0]), v.astype(np.float32).tobytes()) for t, v in zip(textos, vetores)],
        )
        self.conexao.commit()

    def fechar(self) -> None:
        self.conexao.close()


def vetorizar_com_cache(
    embedder: Embedder,
    cache: CacheDeEmbeddings,
    textos: list[str],
    *,
    tamanho_lote: int,
    pausa: float = 0.0,
    dormir: Dormir = time.sleep,
    escrever: Callable[[str], None] = print,
) -> tuple[np.ndarray, int]:
    """Vetoriza os textos, reaproveitando o cache. Devolve a matriz e quantos textos foram calculados agora."""
    resultado = np.zeros((len(textos), embedder.dimensions), dtype=np.float32)
    achados = cache.obter(textos)
    for i, vetor in achados.items():
        resultado[i] = vetor
    faltando = [i for i in range(len(textos)) if i not in achados]
    for inicio in range(0, len(faltando), tamanho_lote):
        indices = faltando[inicio : inicio + tamanho_lote]
        lote = [textos[i] for i in indices]
        vetores = embedder.embed_documentos(lote)
        cache.guardar(lote, vetores)  # grava já: uma queda no próximo lote não perde este
        for i, vetor in zip(indices, vetores):
            resultado[i] = vetor
        escrever(f"  vetorizados {min(inicio + tamanho_lote, len(faltando))}/{len(faltando)}")
        if pausa and inicio + tamanho_lote < len(faltando):
            dormir(pausa)
    return resultado, len(faltando)


# --------------------------------------------------------------------------- construção


# A rotina de repetição ficou em src/fsutil.py; o nome antigo continua valendo aqui.
_com_repeticao = com_repeticao


def ler_chunks(jsonl: Path) -> list[dict[str, Any]]:
    return [json.loads(linha) for linha in jsonl.read_text(encoding="utf-8").splitlines() if linha.strip()]


def _construir_fts(chunks: list[dict[str, Any]], caminho: Path) -> None:
    conexao = sqlite3.connect(caminho)
    try:
        conexao.execute(
            "CREATE VIRTUAL TABLE trechos USING fts5(texto, chunk_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
        )
        conexao.execute("CREATE TABLE metadados (chunk_id TEXT PRIMARY KEY, texto TEXT NOT NULL, metadata TEXT NOT NULL)")
        conexao.executemany(
            "INSERT INTO trechos (texto, chunk_id) VALUES (?, ?)",
            [(c["texto"], c["metadata"]["chunk_id"]) for c in chunks],
        )
        conexao.executemany(
            "INSERT INTO metadados (chunk_id, texto, metadata) VALUES (?, ?, ?)",
            [(c["metadata"]["chunk_id"], c["texto"], json.dumps(c["metadata"], ensure_ascii=False)) for c in chunks],
        )
        conexao.commit()
    finally:
        conexao.close()


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def construir_indice(
    jsonl: Path,
    saida: Path,
    embedder: Embedder,
    cache: CacheDeEmbeddings,
    *,
    tamanho_lote: int = 32,
    pausa: float = 0.0,
    dormir: Dormir = time.sleep,
    escrever: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Constrói (ou reaproveita) o índice de uma base. Devolve o estado de indexação."""
    fonte_sha = _sha256(jsonl)
    arquivo_estado = saida / "estado_indexacao.json"
    if arquivo_estado.exists():
        estado = json.loads(arquivo_estado.read_text(encoding="utf-8"))
        completo = all((saida / nome).exists() for nome in ("chunks.faiss", "chunk_ids.json", "fts.sqlite"))
        if completo and estado.get("fonte_sha256") == fonte_sha and estado.get("identity") == embedder.identity:
            escrever(f"Índice já está atualizado ({estado['total_vetores']} vetores): nada a fazer.")
            return {**estado, "reaproveitado": True, "calculados_agora": 0}

    chunks = ler_chunks(jsonl)
    if not chunks:
        raise ValueError(f"{jsonl.name} não tem trechos")
    ids = [c["metadata"]["chunk_id"] for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("há chunk_id repetido no arquivo de trechos")

    escrever(f"Indexando {len(chunks)} trechos com {embedder.identity}")
    vetores, calculados = vetorizar_com_cache(
        embedder, cache, [c["texto"] for c in chunks],
        tamanho_lote=tamanho_lote, pausa=pausa, dormir=dormir, escrever=escrever,
    )

    # Constrói em uma pasta temporária e troca no fim, para um erro no meio não deixar um índice pela metade.
    temporaria = saida.with_name(saida.name + ".tmp")
    if temporaria.exists():
        _com_repeticao(lambda: shutil.rmtree(temporaria), dormir)
    temporaria.mkdir(parents=True)
    indice = faiss.IndexFlatIP(embedder.dimensions)
    indice.add(vetores)
    # Os bytes são gravados pelo Python: o FAISS não abre caminhos com acento no Windows
    # (por exemplo, C:\Users\João\...), e o repositório pode ser clonado em qualquer pasta.
    (temporaria / "chunks.faiss").write_bytes(faiss.serialize_index(indice).tobytes())
    (temporaria / "chunk_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
    _construir_fts(chunks, temporaria / "fts.sqlite")
    estado = {
        "esquema": ESQUEMA,
        "identity": embedder.identity,
        "dimensao": embedder.dimensions,
        "total_vetores": int(indice.ntotal),
        "fonte": jsonl.name,
        "fonte_sha256": fonte_sha,
        "criado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (temporaria / "estado_indexacao.json").write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    if saida.exists():
        _com_repeticao(lambda: shutil.rmtree(saida), dormir)
    _com_repeticao(lambda: temporaria.rename(saida), dormir)
    return {**estado, "reaproveitado": False, "calculados_agora": calculados}


# --------------------------------------------------------------------------- carregamento


@dataclass
class IndiceCarregado:
    indice: faiss.Index
    chunk_ids: list[str]
    estado: dict[str, Any]
    caminho_fts: Path


def carregar_indice(pasta: Path, identity_esperada: str | None = None) -> IndiceCarregado:
    """Carrega um índice já construído, recusando um incompatível. Nunca reindexa."""
    arquivo_estado = pasta / "estado_indexacao.json"
    if not arquivo_estado.exists():
        raise FileNotFoundError(f"não há índice em {pasta}. Rode: python -m src.index build")
    estado = json.loads(arquivo_estado.read_text(encoding="utf-8"))
    if estado.get("esquema") != ESQUEMA:
        raise IndiceIncompativel(f"esquema de índice desconhecido: {estado.get('esquema')!r}")
    if identity_esperada and estado["identity"] != identity_esperada:
        raise IndiceIncompativel(
            f"o índice foi feito com {estado['identity']}, mas a configuração atual é {identity_esperada}. "
            "Vetores de modelos diferentes não podem ser misturados: reconstrua o índice com `python -m src.index build`."
        )
    indice = faiss.deserialize_index(np.frombuffer((pasta / "chunks.faiss").read_bytes(), dtype=np.uint8))
    ids = json.loads((pasta / "chunk_ids.json").read_text(encoding="utf-8"))
    if indice.ntotal != len(ids) or indice.d != estado["dimensao"]:
        raise IndiceIncompativel(
            f"índice corrompido: {indice.ntotal} vetores (dimensão {indice.d}) para {len(ids)} identificadores"
        )
    return IndiceCarregado(indice, ids, estado, pasta / "fts.sqlite")


# --------------------------------------------------------------------------- linha de comando


def pasta_do_indice(cenario_id: str, base_id: str, dados: Path | None = None) -> Path:
    return (dados or RAIZ / "data") / "index" / cenario_id / base_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.index", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="comando", required=True)
    for nome in ("build", "status"):
        s = sub.add_parser(nome)
        s.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
        s.add_argument("--base", default="editais_normativo")
        if nome == "build":
            s.add_argument("--versao", help="padrão: a versão mais recente coletada")
    args = p.parse_args(argv)
    config = get_settings()
    saida = pasta_do_indice(args.scenario, args.base)

    if args.comando == "status":
        try:
            carregado = carregar_indice(saida, f"{config.embedding_provider}:{config.embedding_model}:{config.embedding_dimensions}")
        except (FileNotFoundError, IndiceIncompativel) as erro:
            print(f"Erro: {erro}", file=sys.stderr)
            return 2
        e = carregado.estado
        print(f"{args.scenario}/{args.base}: {e['total_vetores']} vetores, {e['identity']}, criado em {e['criado_em']}")
        return 0

    pastas = pastas_do_cenario(args.scenario)
    versao = args.versao or (versoes_existentes(pastas) or [None])[-1]
    jsonl = pastas.dados / "processed" / args.scenario / f"{versao}_{args.base}.jsonl"
    if not versao or not jsonl.exists():
        print(f"Erro: não há trechos em {jsonl}. Rode primeiro: python -m src.ingest --scenario {args.scenario}", file=sys.stderr)
        return 2
    try:
        embedder = criar_embedder(config)
        cache = CacheDeEmbeddings(pastas.dados / "index" / "_cache.sqlite", embedder.identity)
        resultado = construir_indice(
            jsonl, saida, embedder, cache,
            tamanho_lote=config.embedding_batch_size, pausa=config.embedding_pause_seconds,
        )
        cache.fechar()
    except (EmbeddingIndisponivel, EmbeddingIncompativel, ValueError) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    print(f"{args.scenario}/{args.base}: {resultado['total_vetores']} vetores ({resultado['calculados_agora']} calculados agora).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
