# Paroquias de Portugal

Repositorio com a lista de paroquias catolicas em Portugal, extraida do site Anuario Catolico de Portugal.

## Ficheiro principal

- `data/paroquias_portugal_anuario_catolico.csv`
- `index.html` para consulta no browser

## Colunas

- `paroquia_id`
- `nome`
- `orago`
- `arciprestado`
- `diocese`
- `url_ficha`
- `site` (vazio por omissao)
- `facebook` (vazio por omissao)
- `instagram` (vazio por omissao)

## Fonte

- https://www.anuariocatolicoportugal.net/lista_paroquias.asp

## Total

- 4373 paroquias (extraidas em 2026-03-19)

## Atualizar dados

```bash
python3 scripts/scrape_paroquias.py
```

## Consulta web

Abrir localmente:

```bash
python3 -m http.server
```

Depois abrir `http://localhost:8000`.

Para publicar no GitHub Pages:

1. Fazer push para o repositório.
2. Em `Settings > Pages`, escolher `Deploy from a branch`.
3. Selecionar a branch `master` e a pasta `/ (root)`.
