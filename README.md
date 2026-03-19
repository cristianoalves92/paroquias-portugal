# Paroquias de Portugal

Repositorio com a lista de paroquias catolicas em Portugal, extraida do site Anuario Catolico de Portugal.

## Ficheiro principal

- `data/paroquias_portugal_anuario_catolico.csv`

## Colunas

- `paroquia_id`
- `nome`
- `orago`
- `arciprestado`
- `diocese`
- `url_ficha`

## Fonte

- https://www.anuariocatolicoportugal.net/lista_paroquias.asp

## Total

- 4373 paroquias (extraidas em 2026-03-19)

## Atualizar dados

```bash
python3 scripts/scrape_paroquias.py
```
