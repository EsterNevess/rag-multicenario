"""Tabela de capacidades de um cenário: o que pode ser perguntado, filtrado, somado e agrupado.

Cada cenário declara em `scenarios/<id>/capacidades.yaml` as suas entidades (tabelas do banco normalizado),
os campos de cada uma, as regras de recusa próprias e os textos que entram nos prompts. O motor (roteador e
geração) lê essa tabela e não tem nenhum nome de campo ou de domínio escrito no código.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parent.parent
TIPOS_DE_CAMPO = ("texto", "categoria", "inteiro", "valor", "data", "id")
_IDENTIFICADOR = re.compile(r"^[a-z_][a-z0-9_]*$")
# Expressões calculadas (por exemplo, o mês de uma data) vêm só deste arquivo, nunca do plano da LLM; mesmo assim
# ficam restritas a funções de texto e data, para um erro de digitação no YAML não virar um comando.
_EXPR_PERMITIDA = re.compile(r"^(substr|strftime|lower|upper)\([a-z0-9_, '%-]*\)$")


class CapacidadesInvalidas(ValueError):
    pass


@dataclass(frozen=True)
class Campo:
    nome: str
    tipo: str  # texto | categoria | inteiro | valor | data | id
    filtravel: bool = True
    agregavel: bool = False  # aceita soma, média, mínimo e máximo
    agrupavel: bool = False
    expr: str | None = None  # expressão SQL quando o campo é calculado
    valores: dict[str, str] | None = None  # campo codificado: código -> rótulo (por exemplo, E -> Estadual)
    listar_valores: bool = False  # mostrar ao analisador os valores que existem no banco (campos de poucos valores)

    @property
    def sql(self) -> str:
        return self.expr or self.nome

    def traduzir(self, valor: Any) -> str | None:
        """Aceita o código ou o rótulo (sem diferença de acento ou caixa) e devolve o código; None se não existe."""
        if not self.valores:
            return str(valor)
        procurado = _simples(str(valor))
        for codigo, rotulo in self.valores.items():
            if procurado in (_simples(codigo), _simples(rotulo)):
                return codigo
        return None

    def valores_validos(self) -> str:
        return ", ".join(f"{c} ({r})" for c, r in (self.valores or {}).items())


def _simples(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower().strip()


@dataclass(frozen=True)
class Entidade:
    tabela: str
    campos: dict[str, Campo]
    exibir: tuple[str, ...]
    ordem_padrao: str


@dataclass(frozen=True)
class RegraDeEntrada:
    categoria: str
    mensagem: str
    padroes: tuple[str, ...]


@dataclass(frozen=True)
class Capacidades:
    cenario: str
    descricao: str  # "contratações públicas do Estado do Rio de Janeiro"
    entidades: dict[str, Entidade]
    regras_de_entrada: tuple[RegraDeEntrada, ...] = ()
    dicas_do_analisador: tuple[str, ...] = ()
    regras_de_resposta: tuple[str, ...] = ()
    catalogo: dict[str, str] = field(default_factory=dict)  # chave: estruturada | normativa | aviso


def _campo(bruto: dict[str, Any], onde: str) -> Campo:
    desconhecidas = set(bruto) - {"nome", "tipo", "filtravel", "agregavel", "agrupavel", "expr", "valores", "listar_valores"}
    if desconhecidas:
        raise CapacidadesInvalidas(f"{onde}: chaves desconhecidas {sorted(desconhecidas)}")
    nome, tipo = bruto.get("nome"), bruto.get("tipo")
    if not isinstance(nome, str) or not _IDENTIFICADOR.match(nome):
        raise CapacidadesInvalidas(f"{onde}: nome de campo inválido: {nome!r}")
    if tipo not in TIPOS_DE_CAMPO:
        raise CapacidadesInvalidas(f"{onde}/{nome}: tipo {tipo!r} (use {TIPOS_DE_CAMPO})")
    expr = bruto.get("expr")
    if expr is not None and not _EXPR_PERMITIDA.match(str(expr)):
        raise CapacidadesInvalidas(f"{onde}/{nome}: expressão não permitida: {expr!r}")
    valores = bruto.get("valores")
    if valores is not None and (not isinstance(valores, dict) or not valores or tipo != "categoria"):
        raise CapacidadesInvalidas(f"{onde}/{nome}: `valores` é um dicionário código -> rótulo e só vale para campo categoria")
    return Campo(
        valores={str(k): str(v) for k, v in valores.items()} if valores else None,
        listar_valores=bool(bruto.get("listar_valores", False)),
        nome=nome,
        tipo=tipo,
        filtravel=bool(bruto.get("filtravel", True)),
        agregavel=bool(bruto.get("agregavel", False)),
        agrupavel=bool(bruto.get("agrupavel", False)),
        expr=expr,
    )


def interpretar(dados: dict[str, Any]) -> Capacidades:
    if not isinstance(dados, dict) or "cenario" not in dados or "entidades" not in dados:
        raise CapacidadesInvalidas("o arquivo precisa ter `cenario` e `entidades`")
    entidades: dict[str, Entidade] = {}
    for nome, bruto in (dados["entidades"] or {}).items():
        if not _IDENTIFICADOR.match(str(nome)):
            raise CapacidadesInvalidas(f"nome de entidade inválido: {nome!r}")
        tabela = bruto.get("tabela", nome)
        if not _IDENTIFICADOR.match(str(tabela)):
            raise CapacidadesInvalidas(f"{nome}: nome de tabela inválido: {tabela!r}")
        campos: dict[str, Campo] = {}
        for item in bruto.get("campos") or []:
            c = _campo(item, nome)
            if c.nome in campos:
                raise CapacidadesInvalidas(f"{nome}: campo repetido: {c.nome}")
            campos[c.nome] = c
        exibir = tuple(bruto.get("exibir") or ())
        ordem = bruto.get("ordem_padrao")
        reais = {c.nome for c in campos.values() if c.expr is None}
        for coluna in (*exibir, ordem):
            if coluna not in reais:
                raise CapacidadesInvalidas(f"{nome}: `exibir` e `ordem_padrao` só aceitam colunas reais, e {coluna!r} não é")
        entidades[nome] = Entidade(tabela=tabela, campos=campos, exibir=exibir, ordem_padrao=ordem)
    if not entidades:
        raise CapacidadesInvalidas("o cenário não declara nenhuma entidade")
    regras = []
    for r in dados.get("regras_de_entrada") or []:
        try:
            padroes = tuple(r["padroes"])
            for p in padroes:
                re.compile(p)
            regras.append(RegraDeEntrada(r["categoria"], r["mensagem"], padroes))
        except (KeyError, TypeError, re.error) as erro:
            raise CapacidadesInvalidas(f"regra de entrada inválida ({erro})") from erro
    return Capacidades(
        cenario=dados["cenario"],
        descricao=dados.get("descricao", dados["cenario"]),
        entidades=entidades,
        regras_de_entrada=tuple(regras),
        dicas_do_analisador=tuple(dados.get("dicas_do_analisador") or ()),
        regras_de_resposta=tuple(dados.get("regras_de_resposta") or ()),
        catalogo=dict(dados.get("catalogo") or {}),
    )


def carregar_capacidades(cenario_id: str, raiz: Path | None = None) -> Capacidades:
    arquivo = (raiz or RAIZ) / "scenarios" / cenario_id / "capacidades.yaml"
    if not arquivo.is_file():
        raise FileNotFoundError(f"o cenário {cenario_id!r} não tem {arquivo.name} (esperado em {arquivo})")
    return interpretar(yaml.safe_load(arquivo.read_text(encoding="utf-8")))


@lru_cache(maxsize=None)
def capacidades_do_cenario(cenario_id: str) -> Capacidades:
    """Versão com cache, para o uso normal; os testes passam `carregar_capacidades` com uma pasta própria."""
    return carregar_capacidades(cenario_id)
