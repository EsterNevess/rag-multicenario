"""Tabela de capacidades de um cenário: o que pode ser perguntado, filtrado, somado e agrupado.

Cada cenário declara em `scenarios/<id>/capacidades.yaml` as suas entidades (tabelas do banco normalizado),
os campos de cada uma, as regras de recusa próprias e os textos que entram nos prompts. O motor (roteador e
geração) lê essa tabela e não tem nenhum nome de campo ou de domínio escrito no código.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import cache
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
    apelidos: dict[str, str] | None = None  # apelido ou sigla -> nome oficial que está no banco (FAPERJ -> Fundação Carlos Chagas Filho...)
    refere: str | None = None  # este campo guarda a chave de outra entidade (trabalho_id refere "trabalho"): liga "desses" ao resultado anterior
    base_ate: str | None = None  # campo de data (AAAA-MM-DD): a base não traz notícia depois dessa data; filtro com data posterior ganha aviso na evidência

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

    def canonico(self, valor: Any) -> Any:
        """Troca um apelido pelo nome oficial gravado no banco; o que não é apelido passa sem mudar."""
        if self.apelidos and isinstance(valor, str):
            procurado = _simples(valor)
            for apelido, oficial in self.apelidos.items():
                if procurado == _simples(apelido):
                    return oficial
        return valor

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
    chave: str | None = None  # o campo que identifica cada linha (id do trabalho); quem a memória de conversa guarda


@dataclass(frozen=True)
class RegraDeEntrada:
    categoria: str
    mensagem: str
    padroes: tuple[str, ...]


@dataclass(frozen=True)
class RegraDeSaida:
    """Se a resposta final casar com um padrão, ela é bloqueada e vira uma recusa com a mensagem da regra."""

    categoria: str
    mensagem: str
    padroes: tuple[str, ...]


NIVEIS_DE_SENSIBILIDADE = ("publico", "interno", "restrito")


@dataclass(frozen=True)
class Capacidades:
    cenario: str
    descricao: str  # "contratações públicas do Estado do Rio de Janeiro"
    entidades: dict[str, Entidade]
    regras_de_entrada: tuple[RegraDeEntrada, ...] = ()
    dicas_do_analisador: tuple[str, ...] = ()
    regras_de_resposta: tuple[str, ...] = ()
    catalogo: dict[str, str] = field(default_factory=dict)  # chave: estruturada | normativa | aviso
    # Níveis de sensibilidade que a busca de documentos pode devolver. Por padrão só o público.
    niveis_consultaveis: tuple[str, ...] = ("publico",)
    regras_de_saida: tuple[RegraDeSaida, ...] = ()
    redacoes_na_ingestao: tuple[dict[str, Any], ...] = ()  # brutas; src.redacao as compila
    catalogo_de_indicadores: Any = None  # src.indicadores.Catalogo, quando o cenário tem indicadores.yaml
    # Campos de metadado dos documentos que o analisador pode filtrar (nome -> o que significa). Texto parcial, sem acento.
    filtros_de_documentos: dict[str, str] = field(default_factory=dict)

    def entidades_agregadas(self) -> dict[str, Entidade]:
        """Cada indicador como uma entidade consultável (a tabela ind_<id> da base agregada)."""
        if self.catalogo_de_indicadores is None:
            return {}
        from src.indicadores import entidades_agregadas

        return entidades_agregadas(self.catalogo_de_indicadores, self)

    def tipo_da_chave(self, entidade: str) -> str | None:
        """De que entidade é a chave que identifica as linhas de `entidade`: a de `trabalho_id` é "trabalho", a de `id` é a própria."""
        ent = self.entidades.get(entidade)
        if ent is None or ent.chave is None:
            return None
        return ent.campos[ent.chave].refere or entidade

    def campo_de_ligacao(self, destino: str, tipo_da_chave: str) -> str | None:
        """O campo da entidade `destino` que guarda chaves do tipo dado (por exemplo, "trabalho"); None se ela não se liga a esse tipo."""
        alvo = self.entidades.get(destino)
        if alvo is None:
            return None
        if self.tipo_da_chave(destino) == tipo_da_chave:
            return alvo.chave
        return next((c.nome for c in alvo.campos.values() if c.refere == tipo_da_chave), None)


def _campo(bruto: dict[str, Any], onde: str) -> Campo:
    desconhecidas = set(bruto) - {"nome", "tipo", "filtravel", "agregavel", "agrupavel", "expr", "valores", "listar_valores", "apelidos", "refere", "base_ate"}
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
    apelidos = bruto.get("apelidos")
    if apelidos is not None and (not isinstance(apelidos, dict) or not apelidos or tipo != "categoria" or bruto.get("valores")):
        raise CapacidadesInvalidas(f"{onde}/{nome}: `apelidos` é um dicionário apelido -> nome oficial e só vale para campo categoria sem `valores`")
    valores = bruto.get("valores")
    if valores is not None and (not isinstance(valores, dict) or not valores or tipo != "categoria"):
        raise CapacidadesInvalidas(f"{onde}/{nome}: `valores` é um dicionário código -> rótulo e só vale para campo categoria")
    refere = bruto.get("refere")
    if refere is not None and not (isinstance(refere, str) and _IDENTIFICADOR.match(refere)):
        raise CapacidadesInvalidas(f"{onde}/{nome}: `refere` é o nome de uma entidade, e veio {refere!r}")
    base_ate = bruto.get("base_ate")
    if base_ate is not None and not (tipo == "data" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(base_ate))):
        raise CapacidadesInvalidas(f"{onde}/{nome}: `base_ate` é uma data AAAA-MM-DD e só vale para campo de tipo data")
    return Campo(
        refere=refere,
        base_ate=str(base_ate) if base_ate else None,
        valores={str(k): str(v) for k, v in valores.items()} if valores else None,
        listar_valores=bool(bruto.get("listar_valores", False)),
        apelidos={str(k): str(v) for k, v in apelidos.items()} if apelidos else None,
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
        chave = bruto.get("chave")
        if chave is not None and (chave not in campos or chave not in exibir):
            raise CapacidadesInvalidas(f"{nome}: `chave` precisa ser um campo real que também está em `exibir`, e veio {chave!r}")
        entidades[nome] = Entidade(tabela=tabela, campos=campos, exibir=exibir, ordem_padrao=ordem, chave=chave)
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
    niveis = tuple(dados.get("niveis_consultaveis") or ("publico",))
    invalidos = [n for n in niveis if n not in NIVEIS_DE_SENSIBILIDADE]
    if invalidos:
        raise CapacidadesInvalidas(f"niveis_consultaveis: {invalidos} (use {NIVEIS_DE_SENSIBILIDADE})")
    saida = []
    for r in dados.get("regras_de_saida") or []:
        try:
            padroes = tuple(r["padroes"])
            for p in padroes:
                re.compile(p)
            saida.append(RegraDeSaida(r["categoria"], r["mensagem"], padroes))
        except (KeyError, TypeError, re.error) as erro:
            raise CapacidadesInvalidas(f"regra de saída inválida ({erro})") from erro
    filtros_docs = dados.get("filtros_de_documentos") or {}
    if not isinstance(filtros_docs, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in filtros_docs.items()):
        raise CapacidadesInvalidas("filtros_de_documentos deve mapear nome -> descrição (texto)")
    redacoes = []
    for r in dados.get("redacoes_na_ingestao") or []:
        try:
            if not r["categoria"] or not r["padroes"]:
                raise KeyError("categoria e padroes são obrigatórios")
            for p in r["padroes"]:
                re.compile(p)
        except (KeyError, TypeError, re.error) as erro:
            raise CapacidadesInvalidas(f"redação na ingestão inválida ({erro})") from erro
        redacoes.append(dict(r))
    return Capacidades(
        niveis_consultaveis=niveis,
        regras_de_saida=tuple(saida),
        redacoes_na_ingestao=tuple(redacoes),
        cenario=dados["cenario"],
        descricao=dados.get("descricao", dados["cenario"]),
        entidades=entidades,
        regras_de_entrada=tuple(regras),
        dicas_do_analisador=tuple(dados.get("dicas_do_analisador") or ()),
        regras_de_resposta=tuple(dados.get("regras_de_resposta") or ()),
        catalogo=dict(dados.get("catalogo") or {}),
        filtros_de_documentos=dict(filtros_docs),
    )


def carregar_capacidades(cenario_id: str, raiz: Path | None = None) -> Capacidades:
    arquivo = (raiz or RAIZ) / "scenarios" / cenario_id / "capacidades.yaml"
    if not arquivo.is_file():
        raise FileNotFoundError(f"o cenário {cenario_id!r} não tem {arquivo.name} (esperado em {arquivo})")
    cap = interpretar(yaml.safe_load(arquivo.read_text(encoding="utf-8")))
    indicadores = arquivo.with_name("indicadores.yaml")
    if indicadores.is_file():
        from dataclasses import replace

        from src.indicadores import interpretar_indicadores

        cap = replace(cap, catalogo_de_indicadores=interpretar_indicadores(yaml.safe_load(indicadores.read_text(encoding="utf-8")), cap))
    return cap


@cache
def capacidades_do_cenario(cenario_id: str) -> Capacidades:
    """Versão com cache, para o uso normal; os testes passam `carregar_capacidades` com uma pasta própria."""
    return carregar_capacidades(cenario_id)
