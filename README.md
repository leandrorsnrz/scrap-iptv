# Lista M3U única — EmbedTV + Embed Canais

Uma única playlist com os canais das duas fontes, atualizada automaticamente.

## Link para o player (raw)

```
https://raw.githubusercontent.com/leandrorsnrz/scrap-iptv/master/canais.m3u
```

Copie esse link e cole no seu player IPTV (VLC, Smarters, TiviMate, OTT Navigator etc.).
O endereço é sempre o mesmo — a lista é regravada por cima a cada atualização,
então você não precisa trocar o link nunca.

## Como é atualizado

Todo o trabalho é feito pelo GitHub Actions, sem precisar do PC ligado:

- `.github/workflows/atualizar-lista.yml` roda **a cada 6 horas** (e também pode ser
  disparado na mão em **Actions → Atualizar lista M3U → Run workflow**).
- O script acessa os dois sites, pega nome, logo, grupo e URL real de stream de cada
  canal, junta tudo em `canais.m3u` e faz commit no repositório.
- A cada rodada as URLs da cache com mais de 24 h são renovadas (`--ttl 24`).

## Usar no PC (opcional)

Clique em `atualizar_tudo.bat` (ou rode no terminal):

```
python playlist_unica.py
python publicar.py
```

`playlist_unica.py` gera a lista e `publicar.py` sobe para o GitHub e mostra o link raw.

### Opções úteis

```
python playlist_unica.py --force        # baixa TODAS as URLs de novo
python playlist_unica.py --ttl 12       # renova URLs com mais de 12 h (0 = nunca)
python playlist_unica.py --workers 6    # mais downloads em paralelo
python playlist_unica.py --sem-dedupe   # mantém canais repetidos entre as fontes
```

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `playlist_unica.py` | Junta as duas fontes e gera `canais.m3u` |
| `embedtv_update.py` | Scraper do EmbedTV (embedtv.cc) |
| `embedcanais_update.py` | Scraper do Embed Canais (embedcanais.online) |
| `publicar.py` | Publica a lista no GitHub e mostra o link raw |
| `atualizar_tudo.bat` | Atalho no Windows: atualiza + publica |
| `canais.m3u` | **A lista pronta** (é esse arquivo que o link raw entrega) |
| `stream_urls.tsv`, `stream_urls_canais.tsv` | Cache de URLs de cada fonte |

## Observações

- Os canais vêm dos dois sites; os grupos são unificados (`Filmes e Séries`,
  `Notícias`, `Canais Abertos`...) e canais repetidos nas duas fontes aparecem uma vez só.
- Canais do Embed Canais precisam do cabeçalho `Referer`; por isso a lista inclui
  `#EXTVLCOPT:http-referrer=https://embedcanaisdetv.xyz/` antes desses canais.
  Players que ignoram essa linha podem não abrir alguns desses canais.
- Quando um canal sai do site, ele sai da lista; se a extração de um canal falhar,
  a URL antiga da cache continua sendo usada.
