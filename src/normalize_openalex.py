"""Normalização do OpenAlex (cenário de pesquisa): páginas JSON de trabalhos para tabelas SQL.

Gera `data/normalized/<cenário>/<versão>.sqlite` com:

  trabalho              um trabalho por linha (título, ano, citações, financiadores, resumo...)
  producao_instituicao  um par (trabalho, instituição) por linha, para contar e somar por instituição
  financiamento         um par (trabalho, financiador) por linha, para contar e somar por financiador
  metadados             versão, cenário e o relatório da normalização

mais um `<versão>.relatorio.json`. As colunas são as que `scenarios/pesquisa/capacidades.yaml` declara.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from src.fetch_corpus import Pastas, carregar_manifesto
from src.fsutil import com_repeticao
from src.normalize import _data, _inteiro, _numero, _texto

ID_OPENALEX = re.compile(r"^(?:https://openalex\.org/)?(W\d+)$")
MAX_CARACTERES_DO_RESUMO = 8000
MAX_PALAVRAS_CHAVE = 15

COLUNAS_TRABALHO = [
    "id", "doi", "titulo", "ano", "data_publicacao", "tipo", "idioma", "periodico", "acesso_aberto", "status_acesso",
    "citacoes", "fwci", "n_autores", "n_instituicoes", "area", "topico_principal", "palavras_chave", "financiadores", "resumo",
    "retratado", "atualizado_em",
]
COLUNAS_INSTITUICAO = ["trabalho_id", "instituicao_id", "instituicao_nome", "pais", "instituicao_alvo", "ano", "titulo", "tipo", "area", "acesso_aberto", "citacoes"]
COLUNAS_FINANCIAMENTO = ["trabalho_id", "financiador_id", "financiador_nome", "ano", "titulo", "area", "citacoes"]
TIPOS_SQL = {
    "ano": "INTEGER", "acesso_aberto": "INTEGER", "citacoes": "INTEGER", "n_instituicoes": "INTEGER",
    "retratado": "INTEGER", "fwci": "REAL", "n_autores": "INTEGER", "instituicao_alvo": "INTEGER",
}
INDICES = [
    "CREATE INDEX ix_trabalho_ano ON trabalho (ano)",
    "CREATE INDEX ix_trabalho_citacoes ON trabalho (citacoes)",
    "CREATE INDEX ix_instituicao_nome ON producao_instituicao (instituicao_nome, ano)",
    "CREATE INDEX ix_instituicao_trabalho ON producao_instituicao (trabalho_id)",
    "CREATE INDEX ix_financiamento_nome ON financiamento (financiador_nome, ano)",
    "CREATE INDEX ix_financiamento_trabalho ON financiamento (trabalho_id)",
]


def _ddl(nome: str, colunas: list[str], chave: str | None = None) -> str:
    campos = [f"{c} {TIPOS_SQL.get(c, 'TEXT')}{' PRIMARY KEY' if c == chave else ''}" for c in colunas]
    return f"CREATE TABLE {nome} ({', '.join(campos)})"


# --------------------------------------------------------------------------- conversões


def _curto(url: Any) -> str | None:
    """'https://openalex.org/I123' -> 'I123'."""
    texto = _texto(url)
    return texto.rsplit("/", 1)[-1] if texto else None


def reconstruir_resumo(indice: Any) -> str | None:
    """O OpenAlex entrega o resumo como {palavra: [posições]}; devolve o texto na ordem original."""
    if not isinstance(indice, dict) or not indice:
        return None
    posicoes: dict[int, str] = {}
    for palavra, lugares in indice.items():
        for lugar in lugares or []:
            if isinstance(lugar, int) and 0 <= lugar < 100_000:
                posicoes[lugar] = palavra
    if not posicoes:
        return None
    texto = " ".join(posicoes[i] for i in sorted(posicoes))
    return texto[:MAX_CARACTERES_DO_RESUMO]


def _nomes(itens: Any, chave: str, limite: int | None = None) -> list[str]:
    vistos: list[str] = []
    for item in itens or []:
        nome = _texto(item.get(chave)) if isinstance(item, dict) else None
        if nome and nome not in vistos:
            vistos.append(nome)
    return vistos[:limite] if limite else vistos


def converter_trabalho(bruto: dict[str, Any], alvo: frozenset[str] = frozenset()) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Devolve a linha do trabalho e as linhas de instituição e de financiador que dependem dele."""
    topico = bruto.get("primary_topic") or {}
    abertura = bruto.get("open_access") or {}
    fonte = (bruto.get("primary_location") or {}).get("source") or {}
    doi = _texto(bruto.get("doi"))
    financiadores = [f for f in (bruto.get("funders") or []) if isinstance(f, dict) and _texto(f.get("display_name"))]
    # O campo `institutions` da API vem vazio; as instituições estão nas autorias. Os nomes dos autores não são guardados.
    autorias = [a for a in (bruto.get("authorships") or []) if isinstance(a, dict)]
    instituicoes = [
        i for a in autorias for i in (a.get("institutions") or []) if isinstance(i, dict) and _texto(i.get("display_name"))
    ]
    ano = _inteiro(bruto.get("publication_year"))
    titulo = _texto(bruto.get("title") or bruto.get("display_name"), livre=True)
    area = _texto((topico.get("field") or {}).get("display_name"))
    citacoes = _inteiro(bruto.get("cited_by_count"))
    acesso = 1 if abertura.get("is_oa") else 0
    trabalho_id = ID_OPENALEX.match(str(bruto.get("id") or "")).group(1)  # já validado por quem chama

    linha = {
        "id": trabalho_id,
        "doi": doi.replace("https://doi.org/", "") if doi else None,
        "titulo": titulo,
        "ano": ano,
        "data_publicacao": _data(bruto.get("publication_date")),
        "tipo": _texto(bruto.get("type")),
        "idioma": _texto(bruto.get("language")),
        "periodico": _texto(fonte.get("display_name")),
        "acesso_aberto": acesso,
        "status_acesso": _texto(abertura.get("oa_status")),
        "citacoes": citacoes,
        "fwci": _numero(bruto.get("fwci")),
        "n_autores": len(autorias) or None,
        "n_instituicoes": _inteiro(bruto.get("institutions_distinct_count")),
        "area": area,
        "topico_principal": _texto(topico.get("display_name")),
        "palavras_chave": "; ".join(_nomes(bruto.get("keywords"), "display_name", MAX_PALAVRAS_CHAVE)) or None,
        "financiadores": "; ".join(_nomes(financiadores, "display_name")) or None,
        "resumo": _texto(reconstruir_resumo(bruto.get("abstract_inverted_index")), livre=True),
        "retratado": 1 if bruto.get("is_retracted") else 0,
        "atualizado_em": _data(bruto.get("updated_date")),
    }
    ja = set()
    linhas_inst = []
    for i in instituicoes:
        chave = _curto(i.get("id")) or i["display_name"]
        if chave in ja:
            continue
        ja.add(chave)
        linhas_inst.append({
            "trabalho_id": trabalho_id, "instituicao_id": _curto(i.get("id")), "instituicao_nome": _texto(i["display_name"]),
            "pais": _texto(i.get("country_code")), "instituicao_alvo": 1 if _curto(i.get("id")) in alvo else 0, "ano": ano, "titulo": titulo, "tipo": linha["tipo"], "area": area,
            "acesso_aberto": acesso, "citacoes": citacoes,
        })
    ja = set()
    linhas_fin = []
    for f in financiadores:
        chave = _curto(f.get("id")) or f["display_name"]
        if chave in ja:
            continue
        ja.add(chave)
        linhas_fin.append({
            "trabalho_id": trabalho_id, "financiador_id": _curto(f.get("id")), "financiador_nome": _texto(f["display_name"]),
            "ano": ano, "titulo": titulo, "area": area, "citacoes": citacoes,
        })
    return linha, linhas_inst, linhas_fin


# --------------------------------------------------------------------------- leitura e gravação


def instituicoes_alvo(manifesto: dict[str, Any]) -> frozenset[str]:
    """Os identificadores de instituição do filtro `institutions.id:I1|I2|...` do manifesto (as instituições do cenário)."""
    achados: set[str] = set()
    for base in manifesto["bases"]:
        endpoints = (base.get("acesso") or {}).get("endpoints") or {}
        for spec in endpoints.values():
            filtro = str(((spec or {}).get("params") or {}).get("filter", "")) if isinstance(spec, dict) else ""
            for trecho in re.findall(r"institutions\.id:([A-Za-z0-9|]+)", filtro):
                achados.update(trecho.split("|"))
    return frozenset(achados)


def _registros(pasta_versao: Path, bases: list[str]):
    for base_id in bases:
        pasta_base = pasta_versao / base_id
        if not pasta_base.is_dir():
            continue
        for pagina in sorted(pasta_base.rglob("pagina_*.json")):
            conteudo = json.loads(pagina.read_text(encoding="utf-8"))
            yield from (conteudo.get("results", []) if isinstance(conteudo, dict) else conteudo)


def normalizar(manifesto_path: Path, versao: str, pastas: Pastas) -> dict[str, Any]:
    manifesto = carregar_manifesto(manifesto_path)
    bases = [b["base_id"] for b in manifesto["bases"] if b["natureza"] == "estruturada" and b["acesso"]["tipo"] == "rest_api"]
    pasta_versao = pastas.versao(versao)
    if not pasta_versao.is_dir():
        raise FileNotFoundError(f"a versão {versao} não foi coletada")

    alvo = instituicoes_alvo(manifesto)
    trabalhos: dict[str, tuple[dict[str, Any], list, list]] = {}
    lidos = 0
    rejeitados: Counter[str] = Counter()
    exemplos: list[str] = []
    duplicados = 0
    for bruto in _registros(pasta_versao, bases):
        lidos += 1
        m = ID_OPENALEX.match(str(bruto.get("id") or "")) if isinstance(bruto, dict) else None
        if not m:
            rejeitados["trabalho: sem identificador OpenAlex válido"] += 1
            if len(exemplos) < 5:
                exemplos.append(repr(bruto.get("id") if isinstance(bruto, dict) else bruto))
            continue
        convertido = converter_trabalho(bruto, alvo)
        anterior = trabalhos.get(m.group(1))
        if anterior is not None:
            duplicados += 1
            if (convertido[0]["atualizado_em"] or "") <= (anterior[0]["atualizado_em"] or ""):
                continue
        trabalhos[m.group(1)] = convertido
    if not trabalhos:
        raise FileNotFoundError(f"nenhuma página de trabalhos na versão {versao}")

    saida = pastas.dados / "normalized" / pastas.cenario_id
    saida.mkdir(parents=True, exist_ok=True)
    destino = saida / f"{versao}.sqlite"
    temporario = saida / f"{versao}.sqlite.tmp"
    if temporario.exists():
        com_repeticao(temporario.unlink)

    conexao = sqlite3.connect(temporario)
    try:
        conexao.execute(_ddl("trabalho", COLUNAS_TRABALHO, chave="id"))
        conexao.execute(_ddl("producao_instituicao", COLUNAS_INSTITUICAO))
        conexao.execute(_ddl("financiamento", COLUNAS_FINANCIAMENTO))
        conexao.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
        ids = sorted(trabalhos)
        for nome, colunas, indice in (
            ("trabalho", COLUNAS_TRABALHO, 0), ("producao_instituicao", COLUNAS_INSTITUICAO, 1), ("financiamento", COLUNAS_FINANCIAMENTO, 2)
        ):
            marcas = ",".join("?" * len(colunas))
            linhas = []
            for i in ids:
                parte = trabalhos[i][indice]
                for l in (parte if isinstance(parte, list) else [parte]):
                    linhas.append(tuple(l.get(c) for c in colunas))
            conexao.executemany(f"INSERT INTO {nome} ({','.join(colunas)}) VALUES ({marcas})", linhas)
        for comando in INDICES:
            conexao.execute(comando)

        total = {t: conexao.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("trabalho", "producao_instituicao", "financiamento")}
        periodo = conexao.execute("SELECT MIN(data_publicacao), MAX(data_publicacao) FROM trabalho").fetchone()
        sem_instituicao = conexao.execute(
            "SELECT COUNT(*) FROM trabalho WHERE id NOT IN (SELECT trabalho_id FROM producao_instituicao)"
        ).fetchone()[0]
        com_financiador = conexao.execute("SELECT COUNT(*) FROM trabalho WHERE financiadores IS NOT NULL").fetchone()[0]
        com_resumo = conexao.execute("SELECT COUNT(*) FROM trabalho WHERE resumo IS NOT NULL").fetchone()[0]
        por_ano = {str(a): n for a, n in conexao.execute("SELECT ano, COUNT(*) FROM trabalho GROUP BY ano ORDER BY ano")}
        relatorio = {
            "scenario_id": pastas.cenario_id,
            "versao": versao,
            "bases_lidas": bases,
            "tabelas": total,
            "registros_lidos": lidos,
            "rejeitados": dict(rejeitados),
            "exemplos_rejeitados": exemplos,
            "duplicados_ignorados": duplicados,
            "trabalhos_por_ano": por_ano,
            "trabalhos_sem_instituicao_na_resposta": sem_instituicao,
            "trabalhos_com_financiador": com_financiador,
            "trabalhos_com_resumo": com_resumo,
            "instituicoes_alvo": sorted(alvo),
            "periodo_de_publicacao": {"de": periodo[0], "ate": periodo[1]},
        }
        conexao.executemany(
            "INSERT INTO metadados VALUES (?, ?)",
            [("versao", versao), ("cenario", pastas.cenario_id), ("relatorio", json.dumps(relatorio, ensure_ascii=False))],
        )
        conexao.commit()
    finally:
        conexao.close()

    if destino.exists():
        com_repeticao(destino.unlink)
    com_repeticao(lambda: temporario.rename(destino))
    (saida / f"{versao}.relatorio.json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    return relatorio
