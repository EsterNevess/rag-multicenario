"""Coleta, versionamento e inventário do corpus de um cenário.

É o único caminho pelo qual dado entra no repositório: lê o manifesto do cenário
(scenarios/<id>/corpus_manifest.yaml) e grava tudo em uma versão imutável.

    python -m src.fetch_corpus fetch     --scenario rj
    python -m src.fetch_corpus update    --scenario rj
    python -m src.fetch_corpus inventory --scenario rj --versao v1

Garantias:
- Idempotente: rodar duas vezes com o mesmo manifesto não rebaixa o que já foi coletado.
- Retomável: uma queda no meio continua de onde parou.
- Educado: respeita o rate_limit_seg do manifesto.
- Resiliente: uma URL que falha vira uma linha "falhou" no inventário, sem parar a coleta.
- Imutável: uma versão nunca é sobrescrita; uma nova coleta é uma nova versão (update).

Os arquivos baixados ficam em data/raw/<cenário>/<versão>/ e não vão para o Git. O manifesto,
o inventário e o diff vão.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
import yaml

try:  # usa o repositório de certificados do sistema (necessário no Windows para alguns sites do governo)
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover - sem truststore, o requests usa o certifi
    pass

from config import RAIZ
from src.schema import Natureza

USER_AGENT = "rag-multicenario/0.1 (+https://github.com/EsterNevess/rag-multicenario)"
TIPOS_ACESSO = {"rest_api", "http_pdf", "arquivo_local", "snapshot_sqlite", "sintetico"}
EXTENSOES_CONHECIDAS = {".pdf", ".htm", ".html", ".json", ".xml", ".csv", ".txt"}
STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}
TEXTO_422_TRANSITORIO = "Erro ao tentar carregar arquivo"  # o PNCP devolve isto de forma intermitente


class ManifestoInvalido(ValueError):
    pass


class VersaoImutavel(RuntimeError):
    """Tentativa de reaproveitar uma versão com um manifesto diferente."""


class FalhaDeColeta(Exception):
    pass


Dormir = Callable[[float], None]

# --------------------------------------------------------------------------- manifesto


def carregar_manifesto(caminho: Path) -> dict[str, Any]:
    dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or not dados.get("scenario_id"):
        raise ManifestoInvalido("o manifesto precisa de scenario_id")
    bases = dados.get("bases")
    if not isinstance(bases, list) or not bases:
        raise ManifestoInvalido("o manifesto precisa de ao menos uma base")
    vistos: set[str] = set()
    for base in bases:
        base_id = base.get("base_id")
        if not base_id:
            raise ManifestoInvalido("toda base precisa de base_id")
        if base_id in vistos:
            raise ManifestoInvalido(f"base_id repetido: {base_id}")
        vistos.add(base_id)
        if base.get("natureza") not in {n.value for n in Natureza}:
            raise ManifestoInvalido(f"{base_id}: natureza desconhecida: {base.get('natureza')!r}")
        tipo = (base.get("acesso") or {}).get("tipo")
        if tipo not in TIPOS_ACESSO:
            raise ManifestoInvalido(f"{base_id}: tipo de acesso desconhecido: {tipo!r}")
    return dados


def hash_do_manifesto(caminho: Path) -> str:
    """SHA-256 do manifesto, ignorando a diferença entre quebras de linha (CRLF x LF).

    Sem isso, o mesmo arquivo teria hashes diferentes no Windows e no Linux.
    """
    conteudo = Path(caminho).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "sha256:" + hashlib.sha256(conteudo).hexdigest()


# --------------------------------------------------------------------------- caminhos


@dataclass(frozen=True)
class Pastas:
    """Onde ficam os dados de um cenário. Parametrizável para os testes."""

    dados: Path
    cenario: Path
    cenario_id: str

    @property
    def raw(self) -> Path:
        return self.dados / "raw" / self.cenario_id

    @property
    def inventario(self) -> Path:
        return self.dados / "inventory" / self.cenario_id

    def versao(self, versao: str) -> Path:
        return self.raw / versao


def pastas_do_cenario(cenario_id: str) -> Pastas:
    return Pastas(dados=RAIZ / "data", cenario=RAIZ / "scenarios" / cenario_id, cenario_id=cenario_id)


def versoes_existentes(pastas: Pastas) -> list[str]:
    if not pastas.raw.is_dir():
        return []
    achadas = [p.name for p in pastas.raw.iterdir() if p.is_dir() and re.fullmatch(r"v\d+", p.name)]
    return sorted(achadas, key=lambda v: int(v[1:]))


def proxima_versao(pastas: Pastas) -> str:
    existentes = versoes_existentes(pastas)
    return f"v{int(existentes[-1][1:]) + 1}" if existentes else "v1"


# --------------------------------------------------------------------------- registro


@dataclass
class Registro:
    path: str
    base_id: str
    tipo: str
    bytes: int
    sha256: str | None
    origem: str
    coletado_em: str
    status: str  # ok | falhou
    erro: str | None = None


def _agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


class RegistroDaVersao:
    """Um arquivo JSONL por versão: uma linha por tentativa. Vale a última de cada origem."""

    def __init__(self, pasta_versao: Path):
        self.pasta = pasta_versao
        self.arquivo = pasta_versao / "_registro.jsonl"

    def ler(self) -> dict[str, Registro]:
        ultimos: dict[str, Registro] = {}
        if self.arquivo.exists():
            for linha in self.arquivo.read_text(encoding="utf-8").splitlines():
                if linha.strip():
                    registro = Registro(**json.loads(linha))
                    ultimos[registro.origem] = registro
        return ultimos

    def gravar(self, registro: Registro) -> None:
        with self.arquivo.open("a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(asdict(registro), ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- download


def nome_de_url(url: str) -> str:
    """Nome de arquivo seguro e determinístico a partir de uma URL."""
    analisada = urlparse(url)
    partes = [analisada.netloc, *[p for p in analisada.path.split("/") if p]]
    nome = re.sub(r"[^A-Za-z0-9._-]+", "_", "_".join(partes)).strip("_")
    if analisada.query:
        nome += "_" + hashlib.sha1(analisada.query.encode()).hexdigest()[:8]
    if len(nome) <= 140:
        return nome or "arquivo"
    # Nome longo: corta o miolo, mas guarda a extensão (senão o arquivo deixa de ser reconhecido) e um hash da
    # URL inteira (senão duas URLs com o mesmo começo viram o mesmo arquivo).
    sufixo = Path(analisada.path).suffix.lower() if not analisada.query else ""
    sufixo = sufixo if sufixo in EXTENSOES_CONHECIDAS else ""
    resumo = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{nome[: 140 - len(sufixo) - 9]}_{resumo}{sufixo}"


def _extensao(url: str, content_type: str | None) -> str:
    sufixo = Path(urlparse(url).path).suffix.lower()
    if sufixo in EXTENSOES_CONHECIDAS:
        return ""  # a extensão já faz parte do nome derivado da URL
    tipo = (content_type or "").split(";")[0].strip().lower()
    return {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "application/json": ".json",
        "text/csv": ".csv",
        "text/plain": ".txt",
        "application/xml": ".xml",
        "text/xml": ".xml",
    }.get(tipo, ".bin")


def _extensao_pelo_conteudo(caminho: Path) -> str:
    """Decide a extensão olhando os primeiros bytes, quando o servidor não informa o tipo certo.

    O PNCP entrega PDFs com Content-Type application/octet-stream.
    """
    with caminho.open("rb") as arquivo:
        inicio = arquivo.read(16).lstrip().lower()
    if inicio.startswith(b"%pdf-"):
        return ".pdf"
    if inicio.startswith((b"<!doctype html", b"<html")):
        return ".html"
    if inicio.startswith((b"{", b"[")):
        return ".json"
    return ".bin"


def _espera_do_429(resposta: requests.Response, tentativa: int, espera_base: float) -> float:
    """HTTP 429 é o servidor pedindo calma. Vale o `Retry-After` dele; sem ele, uma espera bem maior
    que a dos outros erros (a API do PNCP barrou uma rajada de 1 requisição por segundo)."""
    try:
        return min(float(resposta.headers["Retry-After"]), 300.0)
    except (KeyError, ValueError):
        return min(espera_base * 15 * 2 ** (tentativa - 1), 300.0)


def _identificador(nome: str) -> str:
    """Nome de tabela ou coluna vindo do manifesto: só letras, números e sublinhado (nada de SQL solto)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(nome)):
        raise ManifestoInvalido(f"identificador SQL inválido no manifesto: {nome!r}")
    return str(nome)


def _remover_se_vazia(pasta: Path) -> None:
    """Uma consulta sem registros não deixa uma pasta vazia para trás (rmdir só remove pasta vazia)."""
    try:
        pasta.rmdir()
    except OSError:
        pass


def _transitorio(resposta: requests.Response) -> bool:
    if resposta.status_code in STATUS_TRANSITORIOS:
        return True
    return resposta.status_code == 422 and TEXTO_422_TRANSITORIO in resposta.text[:500]


def baixar(
    sessao: requests.Session,
    url: str,
    destino_sem_extensao: Path,
    *,
    params: dict[str, Any] | None = None,
    tentativas: int = 4,
    espera_base: float = 2.0,
    dormir: Dormir = time.sleep,
) -> tuple[Path, str, int, str | None]:
    """Baixa uma URL com repetição apenas em falhas passageiras.

    Repete: 429, 5xx, 422 do PNCP ("erro ao carregar arquivo"), tempo esgotado e queda de conexão.
    Não repete: 404, 403 e demais erros definitivos. Um 403 é uma recusa do servidor e não é contornado.
    """
    ultimo_erro = "sem resposta"
    for tentativa in range(1, tentativas + 1):
        espera = espera_base * 2 ** (tentativa - 1)
        try:
            with sessao.get(url, params=params, stream=True, timeout=60) as resposta:
                if resposta.ok:
                    destino_sem_extensao.parent.mkdir(parents=True, exist_ok=True)
                    parcial = destino_sem_extensao.with_name(destino_sem_extensao.name + ".part")
                    h = hashlib.sha256()
                    total = 0
                    with parcial.open("wb") as saida:
                        for bloco in resposta.iter_content(chunk_size=1024 * 256):
                            saida.write(bloco)
                            h.update(bloco)
                            total += len(bloco)
                    tipo = resposta.headers.get("content-type")
                    extensao = _extensao(url, tipo)
                    if extensao == ".bin":
                        extensao = _extensao_pelo_conteudo(parcial)
                    destino = destino_sem_extensao.with_name(destino_sem_extensao.name + extensao)
                    parcial.replace(destino)
                    return destino, h.hexdigest(), total, tipo
                ultimo_erro = f"HTTP {resposta.status_code}"
                if resposta.status_code == 429:
                    espera = _espera_do_429(resposta, tentativa, espera_base)
                if not _transitorio(resposta):
                    raise FalhaDeColeta(ultimo_erro)
        except (requests.ConnectionError, requests.Timeout) as erro:
            ultimo_erro = type(erro).__name__
        if tentativa < tentativas:
            dormir(espera)
    raise FalhaDeColeta(f"{ultimo_erro} (após {tentativas} tentativas)")


# --------------------------------------------------------------------------- coleta por base


class Coleta:
    """Coleta uma versão. Recebe a sessão HTTP e o `dormir` por parâmetro para ser testável."""

    def __init__(
        self,
        pastas: Pastas,
        versao: str,
        *,
        sessao: requests.Session | None = None,
        dormir: Dormir = time.sleep,
        pasta_local: Path | None = None,
        max_paginas: int | None = None,
        espera_base: float = 2.0,
        escrever: Callable[[str], None] = print,
        snapshot_db: Path | None = None,
    ):
        self.pastas = pastas
        self.versao = versao
        self.pasta = pastas.versao(versao)
        self.registro = RegistroDaVersao(self.pasta)
        self.sessao = sessao or requests.Session()
        self.sessao.headers["User-Agent"] = USER_AGENT
        self.dormir = dormir
        self.pasta_local = pasta_local
        self.max_paginas = max_paginas
        self.espera_base = espera_base
        self.escrever = escrever
        self.snapshot_db = snapshot_db

    # -- utilidades
    def _relativo(self, caminho: Path) -> str:
        return caminho.relative_to(self.pastas.dados.parent).as_posix()

    def _ja_ok(self, origem: str) -> bool:
        registro = self.registro.ler().get(origem)
        return bool(
            registro
            and registro.status == "ok"
            and (self.pastas.dados.parent / registro.path).exists()
        )

    def _falhou(self, base_id: str, tipo: str, origem: str, erro: str) -> None:
        self.escrever(f"  FALHOU  {origem}: {erro}")
        self.registro.gravar(
            Registro("", base_id, tipo, 0, None, origem, _agora(), "falhou", erro)
        )

    def _ok(self, base_id: str, caminho: Path, sha: str, tamanho: int, origem: str) -> None:
        tipo = caminho.suffix.lstrip(".").lower() or "bin"
        self.escrever(f"  ok      {origem} ({tamanho} bytes)")
        self.registro.gravar(
            Registro(self._relativo(caminho), base_id, tipo, tamanho, sha, origem, _agora(), "ok")
        )

    # -- http_pdf
    def _coletar_url(self, base_id: str, url: str, rate_limit: float) -> None:
        if self._ja_ok(url):
            self.escrever(f"  já tem  {url}")
            return
        destino = self.pasta / base_id / nome_de_url(url)
        try:
            caminho, sha, tamanho, _ = baixar(
                self.sessao, url, destino, espera_base=self.espera_base, dormir=self.dormir
            )
        except FalhaDeColeta as erro:
            self._falhou(base_id, "http", url, str(erro))
        else:
            self._ok(base_id, caminho, sha, tamanho, url)
        if rate_limit:
            self.dormir(rate_limit)

    # -- arquivo local (documentos baixados à mão, conferidos pelo hash do manifesto)
    def _coletar_locais(self, base_id: str, acesso: dict[str, Any]) -> None:
        bloco = acesso.get("arquivos_locais") or {}
        pasta = self.pasta_local or (RAIZ / bloco.get("pasta", ""))
        for item in bloco.get("arquivos", []):
            origem = item.get("origem") or item["arquivo"]
            chave = f"local:{item['arquivo']}"
            if self._ja_ok(chave):
                self.escrever(f"  já tem  {item['arquivo']}")
                continue
            fonte = Path(pasta) / item["arquivo"]
            if not fonte.is_file():
                self._falhou(base_id, "local", chave, f"arquivo local ausente: {fonte}")
                continue
            sha = _sha256_arquivo(fonte)
            esperado = item.get("sha256")
            if esperado and sha != esperado:
                self._falhou(base_id, "local", chave, "sha256 diferente do declarado no manifesto")
                continue
            destino = self.pasta / base_id / item["arquivo"]
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fonte, destino)
            self.escrever(f"  ok      {item['arquivo']} (local, {fonte.stat().st_size} bytes)")
            self.registro.gravar(
                Registro(
                    self._relativo(destino),
                    base_id,
                    destino.suffix.lstrip(".").lower() or "bin",
                    fonte.stat().st_size,
                    sha,
                    chave,
                    _agora(),
                    "ok",
                    None,
                )
            )
            self._anotar_origem(chave, origem)

    def _anotar_origem(self, chave: str, origem: str) -> None:
        """Guarda a origem pública do documento local (a URL de onde ele veio)."""
        anotacoes = self.pasta / "_origens_locais.json"
        dados = json.loads(anotacoes.read_text(encoding="utf-8")) if anotacoes.exists() else {}
        dados[chave] = origem
        anotacoes.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- rest_api paginada
    def _coletar_rest(self, base_id: str, acesso: dict[str, Any]) -> None:
        """Uma base pode declarar um `endpoint` ou vários em `endpoints`; cada um vai para a sua subpasta.

        Um endpoint pode ser só a URL ou um objeto com:
          url             o endereço
          params          parâmetros só deste endpoint (somam-se aos da base)
          iterar          {parâmetro: [valores]}: uma coleta para cada valor (por exemplo, as 13 modalidades)
          por_orgao_de    nome de outro endpoint da base: uma coleta para cada órgão que ele devolveu
          parametro_orgao nome do parâmetro que recebe o CNPJ do órgão (padrão: cnpjOrgao)

        Em `iterar`, uma chave que começa com "_" não vai para a URL: serve só de nome da subpasta e para
        preencher `{chave}` dentro dos outros parâmetros (por exemplo, o ano dentro de um filtro).
        """
        endpoints: dict[str, dict[str, Any]] = {}
        for nome, valor in (acesso.get("endpoints") or {}).items():
            endpoints[nome] = valor if isinstance(valor, dict) else {"url": valor}
        if "endpoint" in acesso:
            endpoints.setdefault("dados", {"url": acesso["endpoint"]})
        for nome, spec in endpoints.items():
            origem_orgaos = spec.get("por_orgao_de")
            if origem_orgaos and origem_orgaos not in endpoints:
                raise ValueError(f"{base_id}/{nome}: por_orgao_de aponta para um endpoint que não existe: {origem_orgaos!r}")
            for sufixo, extras in self._combinacoes(base_id, spec):
                pasta = f"{nome}/{sufixo}" if sufixo else nome
                self._coletar_endpoint(base_id, pasta, spec["url"], acesso, self._parametros(spec, extras))

    @staticmethod
    def _parametros(spec: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
        """Junta os parâmetros do endpoint e os da combinação, preenchendo {chave} e tirando as chaves "_"."""
        internos = {k[1:]: v for k, v in extras.items() if k.startswith("_")}
        params = {**(spec.get("params") or {}), **{k: v for k, v in extras.items() if not k.startswith("_")}}
        for chave, valor in params.items():
            if isinstance(valor, str):
                for nome, interno in internos.items():
                    valor = valor.replace("{" + nome + "}", str(interno))
                params[chave] = valor
        return params

    def _combinacoes(self, base_id: str, spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Todas as combinações de parâmetros que um endpoint pede, cada uma com o nome da sua subpasta."""
        listas: dict[str, list[Any]] = {k: list(v) for k, v in (spec.get("iterar") or {}).items()}
        if spec.get("por_orgao_de"):
            listas[spec.get("parametro_orgao", "cnpjOrgao")] = self._orgaos_de(base_id, spec["por_orgao_de"])
        if not listas:
            return [("", {})]
        combinacoes = []
        for valores in itertools.product(*listas.values()):
            extras = dict(zip(listas.keys(), valores, strict=False))
            sufixo = "_".join(f"{k}-{v}" for k, v in extras.items())
            combinacoes.append((sufixo, extras))
        return combinacoes

    def _orgaos_de(self, base_id: str, nome_endpoint: str) -> list[str]:
        """CNPJs distintos dos órgãos que apareceram nas páginas já coletadas de outro endpoint."""
        orgaos: set[str] = set()
        for pagina in sorted((self.pasta / base_id / nome_endpoint).rglob("pagina_*.json")):
            conteudo = json.loads(pagina.read_text(encoding="utf-8"))
            for item in (conteudo.get("data", []) if isinstance(conteudo, dict) else conteudo):
                cnpj = (item.get("orgaoEntidade") or {}).get("cnpj")
                if cnpj:
                    orgaos.add(cnpj)
        self.escrever(f"  {len(orgaos)} órgão(s) encontrados em {nome_endpoint}")
        return sorted(orgaos)

    def _coletar_endpoint(
        self, base_id: str, pasta: str, endpoint: str, acesso: dict[str, Any], params_extra: dict[str, Any] | None = None
    ) -> None:
        params_base = {**(acesso.get("params") or {}), **(params_extra or {})}
        pag = acesso.get("paginacao") or {}
        campo = pag.get("campo", "pagina")
        tamanho = pag.get("tamanho", 50)
        campo_tamanho = pag.get("campo_tamanho", "tamanhoPagina")
        inicio = pag.get("inicio", 1)
        campo_itens = pag.get("campo_itens", "data")
        limite_paginas = pag.get("max_paginas")
        rate_limit = float(acesso.get("rate_limit_seg") or 0)

        def vazio(origem: str) -> None:
            self.registro.gravar(Registro("", base_id, "http", 0, None, origem, _agora(), "vazio"))

        pagina = inicio
        while self.max_paginas is None or (pagina - inicio) < self.max_paginas:
            if limite_paginas and (pagina - inicio) >= int(limite_paginas):
                return
            params = {**params_base, campo: pagina, campo_tamanho: tamanho}
            origem = endpoint + "?" + urlencode(sorted(params.items()))
            registro = self.registro.ler().get(origem)
            if registro and registro.status == "vazio":
                return  # já se sabe que esta consulta não tem registros; não pergunta de novo
            if registro and registro.status == "ok" and (self.pastas.dados.parent / registro.path).exists():
                conteudo = json.loads((self.pastas.dados.parent / registro.path).read_text(encoding="utf-8"))
            else:
                destino = self.pasta / base_id / pasta / f"pagina_{pagina:06d}.json"
                try:
                    caminho, sha, tamanho_bytes, _ = baixar(
                        self.sessao, endpoint, destino, params=params,
                        espera_base=self.espera_base, dormir=self.dormir,
                    )
                    if tamanho_bytes == 0:  # HTTP 204: a consulta é válida e não tem nenhum registro
                        caminho.unlink(missing_ok=True)
                        _remover_se_vazia(caminho.parent)
                        if pagina == inicio:
                            vazio(origem)
                        if rate_limit:
                            self.dormir(rate_limit)
                        return
                    conteudo = json.loads(caminho.read_text(encoding="utf-8"))
                except FalhaDeColeta as erro:
                    self._falhou(base_id, "http", origem, str(erro))
                    return
                except json.JSONDecodeError:
                    caminho.unlink(missing_ok=True)
                    self._falhou(base_id, "http", origem, "resposta que não é JSON")
                    return
                itens = conteudo.get(campo_itens, []) if isinstance(conteudo, dict) else conteudo
                if not itens:  # página vazia: acabou, e não guardamos página vazia
                    caminho.unlink(missing_ok=True)
                    _remover_se_vazia(caminho.parent)
                    if pagina == inicio:
                        vazio(origem)
                    if rate_limit:
                        self.dormir(rate_limit)
                    return
                self._ok(base_id, caminho, sha, tamanho_bytes, origem)
                if rate_limit:
                    self.dormir(rate_limit)
            total_paginas = conteudo.get("totalPaginas") if isinstance(conteudo, dict) else None
            if total_paginas and pagina >= int(total_paginas):
                return
            pagina += 1

    # -- snapshot: payloads da API já coletados e guardados em um banco SQLite (por exemplo, pelo projeto em equipe)
    def _importar_snapshot(self, base_id: str, acesso: dict[str, Any]) -> None:
        """Copia os registros de uma tabela de um banco SQLite para páginas JSON no formato da API.

        O banco é aberto só para leitura. A identidade do snapshot é a "impressão digital" do seu conteúdo
        (hash dos hashes de cada registro, em ordem fixa), e não o hash do arquivo, que muda a cada uso do banco.
        """
        tabela = _identificador(acesso.get("tabela", "raw_asset"))
        col_tipo = _identificador(acesso.get("coluna_tipo", "resource_type"))
        col_payload = _identificador(acesso.get("coluna_payload", "payload"))
        col_hash = _identificador(acesso.get("coluna_hash", "payload_hash"))
        col_ordem = _identificador(acesso.get("coluna_ordem", "external_id"))
        tipos: dict[str, str] = acesso["tipos"]
        tamanho = int(acesso.get("tamanho_pagina", 500))
        esperadas = acesso.get("impressao_digital") or {}
        caminho = Path(self.snapshot_db) if self.snapshot_db else RAIZ / acesso["arquivo"]
        chave = f"snapshot:{caminho.name}"
        if not caminho.is_file():
            self._falhou(base_id, "snapshot", chave, f"arquivo do snapshot ausente: {caminho}")
            return

        conexao = sqlite3.connect(caminho.resolve().as_uri() + "?mode=ro", uri=True)
        resumo: dict[str, dict[str, Any]] = {}
        try:
            for tipo, pasta in tipos.items():
                consulta = f"FROM {tabela} WHERE {col_tipo} = ? ORDER BY {col_ordem}, rowid"
                # Passo 1: a impressão digital, antes de gravar qualquer coisa.
                h = hashlib.sha256()
                total = 0
                for (hash_registro,) in conexao.execute(f"SELECT {col_hash} {consulta}", (tipo,)):
                    h.update(str(hash_registro).encode("utf-8") + b"\n")
                    total += 1
                impressao = h.hexdigest()
                if esperadas.get(tipo) and esperadas[tipo] != impressao:
                    self._falhou(base_id, "snapshot", f"{chave}:{tipo}", "impressão digital do snapshot difere da declarada no manifesto")
                    continue
                # Passo 2: as páginas.
                anteriores = self.registro.ler()
                cursor = conexao.execute(f"SELECT {col_payload} {consulta}", (tipo,))
                numero = 0
                paginas_novas = 0
                while True:
                    linhas = cursor.fetchmany(tamanho)
                    if not linhas:
                        break
                    numero += 1
                    conteudo = json.dumps(
                        {"data": [json.loads(payload) for (payload,) in linhas], "fonte": "snapshot", "tipo": tipo},
                        ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8")
                    sha = hashlib.sha256(conteudo).hexdigest()
                    origem = f"snapshot:{tipo}:pagina_{numero:06d}"
                    destino = self.pasta / base_id / pasta / f"pagina_{numero:06d}.json"
                    anterior = anteriores.get(origem)
                    if anterior and anterior.status == "ok" and anterior.sha256 == sha and destino.exists():
                        continue  # já importada, idêntica: não reescreve
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    destino.write_bytes(conteudo)
                    self.registro.gravar(Registro(self._relativo(destino), base_id, "json", len(conteudo), sha, origem, _agora(), "ok"))
                    paginas_novas += 1
                self.escrever(f"  snapshot {tipo}: {total} registros em {numero} páginas ({paginas_novas} gravadas agora)")
                resumo[tipo] = {"linhas": total, "paginas": numero, "impressao_digital": impressao}
        finally:
            conexao.close()
        if resumo:
            (self.pasta / "_snapshot.json").write_text(
                json.dumps(
                    {"base_id": base_id, "arquivo": caminho.name, "tabela": tabela, "tipos": resumo, "importado_em": _agora()},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )

    # -- orquestração
    def coletar_base(self, base: dict[str, Any]) -> None:
        base_id = base["base_id"]
        acesso = base["acesso"]
        tipo = acesso["tipo"]
        self.escrever(f"[{base_id}] ({tipo})")
        if tipo == "http_pdf":
            for item in acesso.get("urls") or []:
                # Uma URL pode ser só o texto ou um objeto com metadados (rotulo, doc_type, data_publicacao...).
                url = item["url"] if isinstance(item, dict) else item
                self._coletar_url(base_id, url, float(acesso.get("rate_limit_seg") or 0))
            self._coletar_locais(base_id, acesso)
        elif tipo == "arquivo_local":
            self._coletar_locais(base_id, acesso)
        elif tipo == "rest_api":
            self._coletar_rest(base_id, acesso)
        elif tipo == "snapshot_sqlite":
            self._importar_snapshot(base_id, acesso)
        else:
            self.escrever("  (base sintética: gerada por script próprio, não coletada aqui)")


# --------------------------------------------------------------------------- inventário


def montar_inventario(manifesto: dict[str, Any], hash_manifesto: str, pastas: Pastas, versao: str) -> dict[str, Any]:
    pasta = pastas.versao(versao)
    registros = list(RegistroDaVersao(pasta).ler().values())
    estado = json.loads((pasta / "_estado.json").read_text(encoding="utf-8"))
    por_base = []
    for base in manifesto["bases"]:
        base_id = base["base_id"]
        meus = [r for r in registros if r.base_id == base_id]
        oks = [r for r in meus if r.status == "ok"]
        formatos: dict[str, int] = {}
        for r in oks:
            formatos[r.tipo] = formatos.get(r.tipo, 0) + 1
        alvo = base.get("volume_alvo")
        por_base.append(
            {
                "base_id": base_id,
                "natureza": base["natureza"],
                "estado": "coletada" if meus else "nao_coletada",
                "motivo_nao_coletada": None if meus else base.get("motivo_nao_coletada"),
                "arquivos": len(oks),
                "formatos": formatos,
                "bytes": sum(r.bytes for r in oks),
                "falhas": len([r for r in meus if r.status == "falhou"]),
                "volume_alvo": alvo,
                "divergencia_alvo": (len(oks) - alvo) if isinstance(alvo, int) and meus else None,
            }
        )
    origens_locais_path = pasta / "_origens_locais.json"
    origens_locais = json.loads(origens_locais_path.read_text(encoding="utf-8")) if origens_locais_path.exists() else {}
    arquivos = [
        {
            "path": r.path,
            "base_id": r.base_id,
            "tipo": r.tipo,
            "bytes": r.bytes,
            "sha256": r.sha256,
            "origem": origens_locais.get(r.origem, r.origem),
            "chave": r.origem,
            "coletado_em": r.coletado_em,
            "status": r.status,
        }
        for r in registros
        if r.status == "ok"
    ]
    arquivos.sort(key=lambda a: (a["base_id"], a["path"]))
    falhas = [{"origem": r.origem, "base_id": r.base_id, "erro": r.erro} for r in registros if r.status == "falhou"]
    snapshot_path = pasta / "_snapshot.json"
    inventario_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else None
    return {
        "scenario_id": manifesto["scenario_id"],
        "version": versao,
        "coletado_em": estado.get("concluida_em") or estado.get("iniciada_em"),
        "manifest_hash": hash_manifesto,
        "totais": {
            "arquivos": len(arquivos),
            "bytes": sum(a["bytes"] for a in arquivos),
            "falhas": len(falhas),
        },
        "por_base": por_base,
        "arquivos": arquivos,
        "falhas": falhas,
        **({"snapshot": inventario_snapshot} if inventario_snapshot else {}),
    }


def _tamanho_legivel(n: int) -> str:
    valor = float(n)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidade == "GB":
            return f"{valor:.0f} {unidade}" if unidade == "B" else f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{n} B"


def inventario_em_markdown(inv: dict[str, Any]) -> str:
    linhas = [
        f"# Inventário do corpus: {inv['scenario_id']} ({inv['version']})",
        "",
        f"Coletado em: {inv['coletado_em']}  ",
        f"Hash do manifesto: `{inv['manifest_hash']}`",
        "",
        f"**Total:** {inv['totais']['arquivos']} arquivos, {_tamanho_legivel(inv['totais']['bytes'])}, "
        f"{inv['totais']['falhas']} falha(s).",
        "",
        "## Por base",
        "",
        "| Base | Natureza | Estado | Arquivos | Formatos | Tamanho | Alvo | Divergência |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for b in inv["por_base"]:
        formatos = ", ".join(f"{k}: {v}" for k, v in sorted(b["formatos"].items())) or "-"
        diverg = b["divergencia_alvo"] if b["divergencia_alvo"] is not None else "-"
        linhas.append(
            f"| {b['base_id']} | {b['natureza']} | {b['estado']} | {b['arquivos']} | {formatos} | "
            f"{_tamanho_legivel(b['bytes'])} | {b['volume_alvo'] if b['volume_alvo'] is not None else '-'} | {diverg} |"
        )
    pendentes = [b for b in inv["por_base"] if b["estado"] == "nao_coletada"]
    if pendentes:
        linhas += ["", "## Bases ainda não coletadas", ""]
        linhas += [f"- `{b['base_id']}`: {b.get('motivo_nao_coletada') or 'sem motivo informado'}" for b in pendentes]
    if inv.get("snapshot"):
        s = inv["snapshot"]
        linhas += ["", "## Snapshot importado", "", f"Arquivo de origem: `{s['arquivo']}` (tabela `{s['tabela']}`), em {s['importado_em']}.", ""]
        linhas += [
            f"- {tipo}: {d['linhas']} registros em {d['paginas']} páginas; impressão digital `{d['impressao_digital'][:16]}…`"
            for tipo, d in s["tipos"].items()
        ]
    linhas += ["", "## Falhas", ""]
    if inv["falhas"]:
        linhas += [f"- `{f['origem']}`: {f['erro']}" for f in inv["falhas"]]
    else:
        linhas.append("Nenhuma.")
    linhas += ["", "## Arquivos", "", "| Arquivo | Base | Tamanho | SHA-256 | Origem |", "|---|---|---|---|---|"]
    for a in inv["arquivos"]:
        linhas.append(
            f"| {Path(a['path']).name} | {a['base_id']} | {_tamanho_legivel(a['bytes'])} | "
            f"`{(a['sha256'] or '')[:12]}` | {a['origem']} |"
        )
    return "\n".join(linhas) + "\n"


def gravar_inventario(inv: dict[str, Any], pastas: Pastas) -> tuple[Path, Path]:
    pastas.inventario.mkdir(parents=True, exist_ok=True)
    caminho_json = pastas.inventario / f"{inv['version']}.json"
    caminho_json.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    pastas.cenario.mkdir(parents=True, exist_ok=True)
    caminho_md = pastas.cenario / "CORPUS_INVENTORY.md"
    caminho_md.write_text(inventario_em_markdown(inv), encoding="utf-8")
    return caminho_json, caminho_md


# --------------------------------------------------------------------------- diff


def diff_inventarios(antigo: dict[str, Any], novo: dict[str, Any]) -> dict[str, list[str]]:
    """Compara duas versões pela origem de cada arquivo: entrou, saiu, mudou, igual."""
    a = {x["origem"]: x["sha256"] for x in antigo["arquivos"]}
    n = {x["origem"]: x["sha256"] for x in novo["arquivos"]}
    return {
        "entrou": sorted(set(n) - set(a)),
        "saiu": sorted(set(a) - set(n)),
        "mudou": sorted(o for o in set(a) & set(n) if a[o] != n[o]),
        "igual": sorted(o for o in set(a) & set(n) if a[o] == n[o]),
    }


def diff_em_markdown(antigo: dict[str, Any], novo: dict[str, Any], diff: dict[str, list[str]]) -> str:
    linhas = [f"# Diferenças entre {antigo['version']} e {novo['version']} ({novo['scenario_id']})", ""]
    titulos = {
        "entrou": "Entraram",
        "saiu": "Saíram",
        "mudou": "Mudaram de conteúdo (hash diferente)",
        "igual": "Permaneceram idênticos",
    }
    for chave in ("entrou", "saiu", "mudou", "igual"):
        linhas += [f"## {titulos[chave]} ({len(diff[chave])})", ""]
        linhas += [f"- `{origem}`" for origem in diff[chave]] or ["Nenhum."]
        linhas.append("")
    return "\n".join(linhas)


def gravar_diff(antigo: dict[str, Any], novo: dict[str, Any], pastas: Pastas) -> Path:
    diff = diff_inventarios(antigo, novo)
    caminho = pastas.inventario / f"DIFF_{antigo['version']}_{novo['version']}.md"
    caminho.write_text(diff_em_markdown(antigo, novo, diff), encoding="utf-8")
    return caminho


# --------------------------------------------------------------------------- comandos


def fetch(
    caminho_manifesto: Path,
    versao: str,
    pastas: Pastas,
    *,
    bases: list[str] | None = None,
    **opcoes: Any,
) -> dict[str, Any]:
    """Coleta (ou retoma) uma versão e gera o inventário."""
    manifesto = carregar_manifesto(caminho_manifesto)
    hash_manifesto = hash_do_manifesto(caminho_manifesto)
    escolhidas = [b for b in manifesto["bases"] if not bases or b["base_id"] in bases]
    if bases:
        desconhecidas = set(bases) - {b["base_id"] for b in manifesto["bases"]}
        if desconhecidas:
            raise ManifestoInvalido(f"base(s) inexistente(s) no manifesto: {sorted(desconhecidas)}")

    pasta = pastas.versao(versao)
    estado_path = pasta / "_estado.json"
    if estado_path.exists():
        estado = json.loads(estado_path.read_text(encoding="utf-8"))
        if estado["manifest_hash"] != hash_manifesto:
            raise VersaoImutavel(
                f"{versao} foi coletada com outro manifesto. Uma versão não é sobrescrita: use `update` para criar a próxima."
            )
    else:
        pasta.mkdir(parents=True, exist_ok=True)
        estado = {"manifest_hash": hash_manifesto, "iniciada_em": _agora(), "concluida_em": None}
        estado_path.write_text(json.dumps(estado, indent=2), encoding="utf-8")

    coleta = Coleta(pastas, versao, **opcoes)
    for base in escolhidas:
        if base.get("coletar") is False:
            # Base declarada, mas ainda não pronta para coleta: não faz nenhuma requisição.
            coleta.escrever(f"[{base['base_id']}] adiada: {base.get('motivo_nao_coletada', 'sem motivo informado')}")
            continue
        coleta.coletar_base(base)

    estado["concluida_em"] = _agora()
    estado_path.write_text(json.dumps(estado, indent=2), encoding="utf-8")
    inventario = montar_inventario(manifesto, hash_manifesto, pastas, versao)
    gravar_inventario(inventario, pastas)
    return inventario


def inventory(caminho_manifesto: Path, versao: str, pastas: Pastas) -> dict[str, Any]:
    manifesto = carregar_manifesto(caminho_manifesto)
    estado_path = pastas.versao(versao) / "_estado.json"
    if not estado_path.exists():
        raise FileNotFoundError(f"a versão {versao} ainda não foi coletada")
    estado = json.loads(estado_path.read_text(encoding="utf-8"))
    inventario = montar_inventario(manifesto, estado["manifest_hash"], pastas, versao)
    gravar_inventario(inventario, pastas)
    return inventario


def update(caminho_manifesto: Path, pastas: Pastas, **opcoes: Any) -> tuple[dict[str, Any], Path | None]:
    """Cria a próxima versão (nunca sobrescreve a anterior) e gera o diff."""
    anterior = (versoes_existentes(pastas) or [None])[-1]
    nova = proxima_versao(pastas)
    inv_anterior = None
    if anterior:
        inv_path = pastas.inventario / f"{anterior}.json"
        inv_anterior = (
            json.loads(inv_path.read_text(encoding="utf-8"))
            if inv_path.exists()
            else inventory(caminho_manifesto, anterior, pastas)
        )
    inv_novo = fetch(caminho_manifesto, nova, pastas, **opcoes)
    caminho_diff = gravar_diff(inv_anterior, inv_novo, pastas) if inv_anterior else None
    return inv_novo, caminho_diff


# --------------------------------------------------------------------------- linha de comando


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.fetch_corpus", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="comando", required=True)
    for nome, ajuda in (
        ("fetch", "coleta inicial (ou retoma) de uma versão"),
        ("update", "cria a próxima versão e gera o diff"),
        ("inventory", "regera o inventário de uma versão"),
    ):
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
        s.add_argument("--manifest", type=Path, help="caminho do manifesto (padrão: o do cenário)")
        if nome in ("fetch", "inventory"):
            s.add_argument("--versao", default="v1")
        if nome == "fetch":
            # Só no fetch: um update parcial deixaria as outras bases "saindo" no diff, o que é enganoso.
            s.add_argument("--base", action="append", dest="bases", help="coletar só esta base (pode repetir)")
        if nome in ("fetch", "update"):
            s.add_argument("--pasta-local", type=Path, help="onde estão os documentos baixados à mão")
            s.add_argument("--snapshot-db", type=Path, help="banco SQLite do snapshot (padrão: o caminho do manifesto)")
            s.add_argument("--max-paginas", type=int, help="limite de páginas em bases rest_api (para testes)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pastas = pastas_do_cenario(args.scenario)
    manifesto = args.manifest or pastas.cenario / "corpus_manifest.yaml"
    try:
        if args.comando == "inventory":
            inv = inventory(manifesto, args.versao, pastas)
        else:
            opcoes = {"pasta_local": args.pasta_local, "max_paginas": args.max_paginas, "snapshot_db": args.snapshot_db}
            if args.comando == "fetch":
                inv = fetch(manifesto, args.versao, pastas, bases=args.bases, **opcoes)
            else:
                inv, caminho_diff = update(manifesto, pastas, **opcoes)
                if caminho_diff:
                    print(f"Diff: {caminho_diff}")
    except (ManifestoInvalido, VersaoImutavel, FileNotFoundError) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    t = inv["totais"]
    print(f"\n{inv['version']}: {t['arquivos']} arquivo(s), {_tamanho_legivel(t['bytes'])}, {t['falhas']} falha(s).")
    print(f"Inventário: data/inventory/{inv['scenario_id']}/{inv['version']}.json e scenarios/{inv['scenario_id']}/CORPUS_INVENTORY.md")
    return 1 if t["falhas"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
