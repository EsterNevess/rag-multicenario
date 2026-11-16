"""Base agregada: indicadores calculados em SQL, gravados com a definição, o recorte e as ressalvas.

Um indicador (por exemplo, "proporção de contratações diretas por ano") é calculado uma vez, a partir do banco
normalizado, e gravado em `data/aggregated/<cenário>/<versão>.sqlite`. A rota agregada lê só essa base: nunca
recalcula na hora e nunca deixa o modelo escolher a definição. Cada indicador carrega o que o define ("contratação
direta = dispensa + inexigibilidade"), de onde veio e as ressalvas, e isso vai junto com o número na evidência.

Os indicadores são declarados em `scenarios/<id>/indicadores.yaml`, de forma estruturada (campo, filtro, métrica),
sem SQL solto: o SQL é montado aqui, com nomes que existem em `capacidades.yaml` e valores como parâmetro.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from config import RAIZ
from src.capacidades import Campo, Capacidades, Entidade, capacidades_do_cenario
from src.fetch_corpus import Pastas, pastas_do_cenario, versoes_existentes
from src.fsutil import com_repeticao

METRICAS = ("contagem", "soma", "media", "razao")
UNIDADES = ("contagem", "proporcao", "reais", "media_reais", "media")
_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class IndicadoresInvalidos(ValueError):
    pass


class BaseDesatualizada(ValueError):
    """A base agregada foi calculada com outra versão do indicadores.yaml."""


@dataclass(frozen=True)
class Predicado:
    campo: str
    em: tuple[Any, ...] = ()
    nao_nulo: bool = False
    igual: Any = None

    def sql(self, campo: Campo) -> tuple[str, list[Any]]:
        if self.nao_nulo:
            return f"{campo.sql} IS NOT NULL", []
        if self.em:
            return f"{campo.sql} IN ({', '.join('?' * len(self.em))})", list(self.em)
        return f"{campo.sql} = ?", [self.igual]

    def texto(self) -> str:
        if self.nao_nulo:
            return f"{self.campo} preenchido"
        if self.em:
            return f"{self.campo} em {list(self.em)}"
        return f"{self.campo} = {self.igual}"


@dataclass(frozen=True)
class Indicador:
    id: str
    nome: str
    descricao: str
    unidade: str
    origem: str  # entidade de capacidades.yaml sobre a qual o indicador é calculado
    dimensoes: tuple[str, ...]
    metrica: str
    campo: str | None = None  # soma e média
    numerador: tuple[Predicado, ...] = ()  # razão
    denominador: tuple[Predicado, ...] = ()
    filtro_fixo: tuple[Predicado, ...] = ()
    definicao: str = ""
    ressalvas: tuple[str, ...] = ()
    exemplos: tuple[tuple[str, dict[str, Any]], ...] = ()  # (pergunta, filtros): mostram ao analisador quando usar o indicador
    palavras_chave: tuple[str, ...] = ()  # assunto do indicador, sem acento e em minúsculas, para a guarda de roteamento
    palavras_de_recorte: tuple[str, ...] = ()  # palavras que mostram que a pergunta quer o recorte extra deste indicador (por exemplo, a esfera)


@dataclass(frozen=True)
class Catalogo:
    indicadores: dict[str, Indicador]
    limites: tuple[str, ...] = ()  # o que o sistema não calcula, e por quê


# --------------------------------------------------------------------------- leitura e validação


def _predicado(bruto: Any, entidade: Entidade, onde: str) -> Predicado:
    if not isinstance(bruto, dict) or "campo" not in bruto:
        raise IndicadoresInvalidos(f"{onde}: o filtro precisa de `campo`")
    desconhecidas = set(bruto) - {"campo", "em", "nao_nulo", "igual"}
    if desconhecidas:
        raise IndicadoresInvalidos(f"{onde}: chaves desconhecidas {sorted(desconhecidas)}")
    if bruto["campo"] not in entidade.campos:
        raise IndicadoresInvalidos(f"{onde}: campo desconhecido {bruto['campo']!r}")
    formas = [k for k in ("em", "nao_nulo", "igual") if k in bruto]
    if len(formas) != 1:
        raise IndicadoresInvalidos(f"{onde}: use exatamente um entre em, nao_nulo e igual")
    em = tuple(bruto["em"]) if "em" in bruto else ()
    if "em" in bruto and not em:
        raise IndicadoresInvalidos(f"{onde}: `em` não pode ser vazio")
    return Predicado(bruto["campo"], em, bool(bruto.get("nao_nulo", False)), bruto.get("igual"))


def _palavras(bruto: Any, onde: str, campo: str = "palavras_chave") -> list[str]:
    palavras = bruto or []
    if not isinstance(palavras, list) or not all(isinstance(p, str) and p for p in palavras):
        raise IndicadoresInvalidos(f"{onde}: `{campo}` é uma lista de textos")
    for p in palavras:
        if p != p.lower() or any(ord(c) > 127 for c in p):
            raise IndicadoresInvalidos(f"{onde}: palavra-chave {p!r} deve estar em minúsculas e sem acento")
    return palavras


def _exemplo(bruto: Any, dimensoes: list[str], onde: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(bruto, dict) or not isinstance(bruto.get("pergunta"), str):
        raise IndicadoresInvalidos(f"{onde}: cada exemplo precisa de `pergunta` (texto) e `filtros`")
    filtros = bruto.get("filtros") or {}
    fora = [k for k in filtros if k not in dimensoes]
    if not isinstance(filtros, dict) or fora:
        raise IndicadoresInvalidos(f"{onde}: o exemplo filtra por {fora}, que não são dimensões do indicador")
    return bruto["pergunta"], dict(filtros)


def interpretar_indicadores(dados: dict[str, Any], cap: Capacidades) -> Catalogo:
    if not isinstance(dados, dict) or not isinstance(dados.get("indicadores"), dict) or not dados["indicadores"]:
        raise IndicadoresInvalidos("o arquivo precisa ter `indicadores`")
    saida: dict[str, Indicador] = {}
    for id_, b in dados["indicadores"].items():
        onde = f"indicador {id_}"
        if not _ID.match(str(id_)):
            raise IndicadoresInvalidos(f"{onde}: id inválido (use minúsculas, números e _)")
        for chave in ("nome", "descricao", "unidade", "origem", "dimensoes", "metrica", "definicao"):
            if not b.get(chave):
                raise IndicadoresInvalidos(f"{onde}: falta `{chave}`")
        if b["unidade"] not in UNIDADES:
            raise IndicadoresInvalidos(f"{onde}: unidade {b['unidade']!r} (use {UNIDADES})")
        if b["metrica"] not in METRICAS:
            raise IndicadoresInvalidos(f"{onde}: métrica {b['metrica']!r} (use {METRICAS})")
        entidade = cap.entidades.get(b["origem"])
        if entidade is None:
            raise IndicadoresInvalidos(f"{onde}: a origem {b['origem']!r} não existe em capacidades.yaml")
        for d in b["dimensoes"]:
            campo = entidade.campos.get(d)
            if campo is None or not campo.agrupavel:
                raise IndicadoresInvalidos(f"{onde}: não dá para agrupar por {d!r} em {b['origem']}")
        campo_metrica = b.get("campo")
        if b["metrica"] in ("soma", "media"):
            c = entidade.campos.get(campo_metrica or "")
            if c is None or not c.agregavel:
                raise IndicadoresInvalidos(f"{onde}: {b['metrica']} exige `campo` agregável em {b['origem']}")
        if b["metrica"] == "razao" and not b.get("numerador"):
            raise IndicadoresInvalidos(f"{onde}: razão exige `numerador`")
        saida[id_] = Indicador(
            id=id_, nome=b["nome"], descricao=b["descricao"], unidade=b["unidade"], origem=b["origem"],
            dimensoes=tuple(b["dimensoes"]), metrica=b["metrica"], campo=campo_metrica,
            numerador=tuple(_predicado(p, entidade, f"{onde}/numerador") for p in b.get("numerador") or ()),
            denominador=tuple(_predicado(p, entidade, f"{onde}/denominador") for p in b.get("denominador") or ()),
            filtro_fixo=tuple(_predicado(p, entidade, f"{onde}/filtro_fixo") for p in b.get("filtro_fixo") or ()),
            definicao=b["definicao"], ressalvas=tuple(b.get("ressalvas") or ()),
            exemplos=tuple(_exemplo(e, b["dimensoes"], onde) for e in b.get("exemplos") or ()),
            palavras_chave=tuple(_palavras(b.get("palavras_chave"), onde)),
            palavras_de_recorte=tuple(_palavras(b.get("palavras_de_recorte"), onde, "palavras_de_recorte")),
        )
    limites = tuple(dados.get("limites") or ())
    for i, ind in saida.items():
        if not all(isinstance(r, str) for r in ind.ressalvas):
            raise IndicadoresInvalidos(f"indicador {i}: cada ressalva precisa ser um texto (cuidado com dois-pontos no meio da frase, que o YAML lê como chave)")
    if not all(isinstance(l, str) for l in limites):
        raise IndicadoresInvalidos("cada item de `limites` precisa ser um texto (cuidado com dois-pontos no meio da frase)")
    return Catalogo(saida, limites)


def carregar_catalogo(cenario: str, cap: Capacidades | None = None, raiz: Path | None = None) -> Catalogo | None:
    """O catálogo do cenário, ou None se ele não declara indicadores."""
    arquivo = (raiz or RAIZ) / "scenarios" / cenario / "indicadores.yaml"
    if not arquivo.is_file():
        return None
    return interpretar_indicadores(yaml.safe_load(arquivo.read_text(encoding="utf-8")), cap or capacidades_do_cenario(cenario))


# --------------------------------------------------------------------------- SQL e entidades


def _tipo_da_dimensao(c: Campo) -> str:
    return "inteiro" if c.tipo == "inteiro" else "categoria"


def entidade_do_indicador(ind: Indicador, cap: Capacidades) -> Entidade:
    """A tabela `ind_<id>` como entidade consultável pelo roteador (mesmas validações e mesmo SQL controlado)."""
    origem = cap.entidades[ind.origem]
    campos: dict[str, Campo] = {}
    for d in ind.dimensoes:
        campos[d] = Campo(d, _tipo_da_dimensao(origem.campos[d]), agrupavel=True, listar_valores=origem.campos[d].tipo == "categoria",
                          apelidos=origem.campos[d].apelidos)
    somavel = ind.metrica in ("contagem", "soma")  # somar uma média ou uma razão entre linhas daria um número sem sentido
    campos["valor"] = Campo("valor", "valor", agregavel=somavel)
    campos["n"] = Campo("n", "inteiro", agregavel=ind.metrica == "contagem")
    if ind.metrica == "razao":
        campos["numerador"] = Campo("numerador", "inteiro")
        campos["denominador"] = Campo("denominador", "inteiro")
    exibir = (*ind.dimensoes, "valor", "n", *(("numerador", "denominador") if ind.metrica == "razao" else ()))
    return Entidade(tabela=f"ind_{ind.id}", campos=campos, exibir=exibir, ordem_padrao=ind.dimensoes[0])


def entidades_agregadas(catalogo: Catalogo, cap: Capacidades) -> dict[str, Entidade]:
    return {i: entidade_do_indicador(ind, cap) for i, ind in catalogo.indicadores.items()}


def montar_sql(ind: Indicador, cap: Capacidades) -> tuple[str, list[Any]]:
    origem = cap.entidades[ind.origem]
    dims = [origem.campos[d] for d in ind.dimensoes]
    selecao = [f"{c.sql} AS {c.nome}" for c in dims]
    parametros: list[Any] = []
    if ind.metrica == "contagem":
        selecao += ["COUNT(*) AS valor", "COUNT(*) AS n"]
    elif ind.metrica in ("soma", "media"):
        c = origem.campos[ind.campo or ""]
        funcao = "SUM" if ind.metrica == "soma" else "AVG"
        selecao += [f"{funcao}({c.sql}) AS valor", f"COUNT({c.sql}) AS n"]
    else:
        partes, params_num = [], []
        for p in ind.numerador:
            s, v = p.sql(origem.campos[p.campo])
            partes.append(s)
            params_num += v
        condicao = " AND ".join(partes)
        selecao += [f"1.0 * SUM(CASE WHEN {condicao} THEN 1 ELSE 0 END) / COUNT(*) AS valor", "COUNT(*) AS n",
                    f"SUM(CASE WHEN {condicao} THEN 1 ELSE 0 END) AS numerador", "COUNT(*) AS denominador"]
        parametros += params_num + params_num  # o numerador aparece duas vezes na seleção
    onde, params_onde = [], []
    for p in (*ind.filtro_fixo, *ind.denominador):
        s, v = p.sql(origem.campos[p.campo])
        onde.append(s)
        params_onde += v
    sql = f"SELECT {', '.join(selecao)} FROM {origem.tabela}"
    if onde:
        sql += " WHERE " + " AND ".join(onde)
    if dims:
        sql += " GROUP BY " + ", ".join(c.sql for c in dims) + " ORDER BY " + ", ".join(c.sql for c in dims)
    return sql, parametros + params_onde


# --------------------------------------------------------------------------- construção


def hash_do_catalogo(cenario: str, raiz: Path | None = None) -> str:
    """SHA-256 do indicadores.yaml, sem diferença entre quebras de linha (CRLF x LF)."""
    arquivo = (raiz or RAIZ) / "scenarios" / cenario / "indicadores.yaml"
    conteudo = arquivo.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(conteudo).hexdigest()


def conferir_atualizacao(banco: Path, cenario: str, raiz: Path | None = None) -> None:
    """Recusa uma base agregada calculada com outra versão do catálogo (definição ou ressalva já mudou)."""
    c = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    try:
        gravado = json.loads(c.execute("SELECT valor FROM metadados WHERE chave = 'relatorio'").fetchone()[0]).get("catalogo_sha256")
    finally:
        c.close()
    if gravado != hash_do_catalogo(cenario, raiz):
        raise BaseDesatualizada(
            f"a base agregada {banco.name} foi calculada com outra versão de indicadores.yaml. Rode: python -m src.indicadores build --scenario {cenario}")


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _ddl(ind: Indicador, cap: Capacidades) -> str:
    origem = cap.entidades[ind.origem]
    colunas = [f"{d} {'INTEGER' if origem.campos[d].tipo == 'inteiro' else 'TEXT'}" for d in ind.dimensoes]
    colunas += ["valor REAL", "n INTEGER"]
    if ind.metrica == "razao":
        colunas += ["numerador INTEGER", "denominador INTEGER"]
    return f"CREATE TABLE ind_{ind.id} ({', '.join(colunas)})"


def construir(cenario: str, versao: str, pastas: Pastas, catalogo: Catalogo | None = None, cap: Capacidades | None = None) -> dict[str, Any]:
    cap = cap or capacidades_do_cenario(cenario)
    catalogo = catalogo or carregar_catalogo(cenario, cap)
    if catalogo is None:
        raise FileNotFoundError(f"o cenário {cenario!r} não tem indicadores.yaml")
    origem_db = pastas.dados / "normalized" / cenario / f"{versao}.sqlite"
    if not origem_db.is_file():
        raise FileNotFoundError(f"não há banco normalizado em {origem_db}. Rode: python -m src.normalize --scenario {cenario}")
    saida = pastas.dados / "aggregated" / cenario
    saida.mkdir(parents=True, exist_ok=True)
    destino, temporario = saida / f"{versao}.sqlite", saida / f"{versao}.sqlite.tmp"
    if temporario.exists():
        com_repeticao(temporario.unlink)

    fonte = sqlite3.connect(f"file:{origem_db.as_posix()}?mode=ro", uri=True)
    novo = sqlite3.connect(temporario)
    relatorio: dict[str, Any] = {}
    try:
        meta_fonte = dict(fonte.execute("SELECT chave, valor FROM metadados").fetchall())
        novo.execute(
            "CREATE TABLE indicadores (id TEXT PRIMARY KEY, nome TEXT, descricao TEXT, unidade TEXT, origem TEXT, dimensoes TEXT, metrica TEXT, "
            "campo TEXT, filtro_fixo TEXT, definicao TEXT, ressalvas TEXT, linhas INTEGER, sql TEXT)")
        novo.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
        linhas_por_indicador: dict[str, int] = {}
        for id_ in sorted(catalogo.indicadores):
            ind = catalogo.indicadores[id_]
            sql, params = montar_sql(ind, cap)
            cursor = fonte.execute(sql, params)
            linhas = cursor.fetchall()
            novo.execute(_ddl(ind, cap))
            if linhas:
                nomes = [d[0] for d in cursor.description]
                novo.executemany(f"INSERT INTO ind_{ind.id} ({', '.join(nomes)}) VALUES ({','.join('?' * len(nomes))})", linhas)
            linhas_por_indicador[id_] = len(linhas)
            novo.execute(
                "INSERT INTO indicadores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ind.id, ind.nome, ind.descricao, ind.unidade, ind.origem, json.dumps(list(ind.dimensoes)), ind.metrica, ind.campo,
                 json.dumps([p.texto() for p in ind.filtro_fixo], ensure_ascii=False), ind.definicao,
                 json.dumps(list(ind.ressalvas), ensure_ascii=False), len(linhas), sql),
            )
        periodo = json.loads(meta_fonte.get("relatorio", "{}")).get("periodo_de_publicacao")
        relatorio = {
            "scenario_id": cenario, "versao": versao, "indicadores": len(catalogo.indicadores), "linhas_por_indicador": linhas_por_indicador,
            "catalogo_sha256": hash_do_catalogo(cenario),
            "calculado_sobre": {"arquivo": origem_db.name, "sha256": _sha256(origem_db), "periodo_de_publicacao": periodo},
            "limites_conhecidos": list(catalogo.limites),
        }
        novo.executemany("INSERT INTO metadados VALUES (?, ?)",
                         [("versao", versao), ("cenario", cenario), ("relatorio", json.dumps(relatorio, ensure_ascii=False))])
        novo.commit()
    finally:
        novo.close()
        fonte.close()
    if destino.exists():
        com_repeticao(destino.unlink)
    com_repeticao(lambda: temporario.rename(destino))
    (saida / f"{versao}.relatorio.json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    return relatorio


def localizar_agregado(pastas: Pastas, versao: str | None = None) -> Path:
    pasta = pastas.dados / "aggregated" / pastas.cenario_id
    for v in ([versao] if versao else list(reversed(versoes_existentes(pastas)))):
        if (pasta / f"{v}.sqlite").is_file():
            return pasta / f"{v}.sqlite"
    raise FileNotFoundError(f"nenhuma base agregada em {pasta}. Rode: python -m src.indicadores build --scenario {pastas.cenario_id}")


def ler_ficha(banco: Path, indicador_id: str) -> dict[str, Any]:
    """Definição, unidade e ressalvas de um indicador, como foram gravadas na construção."""
    c = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        linha = c.execute("SELECT * FROM indicadores WHERE id = ?", (indicador_id,)).fetchone()
        meta = dict(c.execute("SELECT chave, valor FROM metadados").fetchall())
    finally:
        c.close()
    if linha is None:
        raise KeyError(indicador_id)
    ficha = dict(linha)
    for k in ("dimensoes", "filtro_fixo", "ressalvas"):
        ficha[k] = json.loads(ficha[k])
    ficha["calculado_sobre"] = json.loads(meta["relatorio"])["calculado_sobre"]
    ficha["versao"] = meta["versao"]
    return ficha


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.indicadores", description=__doc__.split("\n\n")[0])
    p.add_argument("acao", choices=("build",))
    p.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--versao", help="padrão: a versão mais recente com banco normalizado")
    args = p.parse_args(argv)
    pastas = pastas_do_cenario(args.scenario)
    versao = args.versao or next((v for v in reversed(versoes_existentes(pastas)) if (pastas.dados / "normalized" / args.scenario / f"{v}.sqlite").is_file()), None)
    if not versao:
        print("Erro: nenhuma versão com banco normalizado.", file=sys.stderr)
        return 2
    try:
        r = construir(args.scenario, versao, pastas)
    except (FileNotFoundError, IndicadoresInvalidos) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    print(f"{args.scenario}/{versao}: {r['indicadores']} indicadores, linhas por indicador: {r['linhas_por_indicador']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
