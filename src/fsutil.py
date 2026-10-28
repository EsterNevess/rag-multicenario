"""Operações de arquivo e pasta que o Windows às vezes nega por um instante."""

from __future__ import annotations

import time
from typing import Any, Callable

Dormir = Callable[[float], None]


def com_repeticao(operacao: Callable[[], Any], dormir: Dormir = time.sleep, tentativas: int = 6) -> Any:
    """Repete uma operação de pasta que o Windows nega por um instante.

    Logo depois de gravar arquivos novos, o antivírus ou o indexador do Windows os examina e segura a
    pasta; renomeá-la ou apagá-la nesse intervalo dá "Acesso negado" (WinError 5). Esperar um pouco resolve.
    Só repete o erro de permissão; qualquer outro erro sobe na hora.
    """
    for tentativa in range(1, tentativas + 1):
        try:
            return operacao()
        except PermissionError:
            if tentativa == tentativas:
                raise
            dormir(0.25 * 2 ** (tentativa - 1))
