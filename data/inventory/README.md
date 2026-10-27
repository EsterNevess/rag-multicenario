# data/inventory

Inventário de cada coleta, por cenário: `data/inventory/<cenário>/vN.json` e `DIFF_vN_vM.md`. O inventário legível de cada cenário fica em `scenarios/<cenário>/CORPUS_INVENTORY.md`.

Os arquivos baixados ficam em `data/raw/<cenário>/vN/`, que o Git ignora. Quem quiser reconstruí-los roda `python -m src.fetch_corpus fetch --scenario <cenário>`.
