# beto

Detector de **surebets (arbitragem)** entre casas de apostas brasileiras com alertas no Telegram.

```
beto ui           # interface web (Streamlit): configuração, coleta, surebets, Telegram
beto collect      # raspa todas as casas e imprime o relatório de cobertura
beto scan         # coleta + detecta oportunidades de arbitragem
beto run          # loop contínuo com alertas no Telegram (ou console)
beto brasileirao  # jogos do Brasileirão + odds 1X2 via RapidAPI (CSV/JSON)
```

---

## Como funciona

A arbitragem acontece quando, tomando a **melhor odd de cada resultado** entre várias casas, a soma
dos inversos fica **< 1** — garantindo lucro independente do resultado.

**Exemplo (1X2):** `home 2.10` (betano) + `draw 3.60` (novibet) + `away 4.20` (novibet)

```
arb_sum = 1/2.10 + 1/3.60 + 1/4.20 ≈ 0.9921  →  lucro ≈ 0,80%
```

Com bankroll de R$ 1.000: stakes ≈ R$ 480 / R$ 278 / R$ 238; retorno garantido ≈ R$ 1.008.

---

## Instalação

```bash
# com uv (recomendado)
pip install uv
uv sync --extra dev

# instala o Chromium para scrapers com JavaScript
uv run playwright install chromium

# sem uv
pip install -e ".[dev]"
playwright install chromium
```

Copie `.env.example` para `.env` e configure:

```bash
cp .env.example .env
```

---

## Configuração (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `BETO_TELEGRAM_BOT_TOKEN` | — | Token do bot (obrigatório para `beto run`) |
| `BETO_TELEGRAM_CHAT_ID` | — | ID do chat que recebe os alertas |
| `BETO_ENABLED_HOUSES` | `["mock"]` | Casas a monitorar (JSON array) |
| `BETO_SCRAPE_INTERVAL_S` | `60` | Intervalo entre ciclos (segundos) |
| `BETO_MIN_PROFIT_PCT` | `0.5` | Threshold mínimo de lucro (%) |
| `BETO_BANKROLL` | `1000.0` | Banca para cálculo de stakes (R$) |
| `BETO_ALERTER` | `telegram` | `telegram` ou `console` (dry-run) |
| `BETO_HEADLESS` | `true` | Playwright em modo headless |
| `BETO_LOG_LEVEL` | `INFO` | Nível de log |

---

## Interface web (Streamlit)

Toda a plataforma também pode ser operada por uma interface gráfica:

```bash
uv sync --extra ui          # instala o Streamlit
uv run beto ui              # abre em http://localhost:8501
# ou: uv run beto ui --port 8600
```

Quatro abas:

| Aba | O que faz |
|---|---|
| **⚙️ Configuração** | Liga/desliga casas, edita filtros de competição, arbitragem (lucro mínimo, banca), matching (limiar fuzzy, janela), scraping (delays, headless) e salva tudo em `.env` |
| **📡 Coleta** | Roda uma coleta e mostra o relatório de cobertura (casa · status · eventos · quotes · tempo) |
| **💰 Surebets** | Coleta + detecta arbitragem e lista cada oportunidade com lucro %, odds e distribuição de stakes |
| **📨 Telegram** | Descobre o `chat_id` (via `getUpdates`), envia um alerta de teste e escolhe o modo de alerta |

> A interface é a fonte de verdade durante a interação; o botão **Salvar em .env**
> persiste a configuração. Para scraping com Playwright (casas JS-pesadas) a CLI
> `beto collect` é mais robusta; a interface funciona bem para configuração, `mock`
> e casas via httpx (betano, sportingbet).

## Uso

### Relatório de cobertura

```bash
uv run beto collect
uv run beto collect --houses mock betano novibet
uv run beto collect --houses betano --debug-dump   # salva payloads brutos em debug/
```

Saída de exemplo:

```
RELATÓRIO DE COBERTURA — Copa do Mundo 2026 — futebol
mercados: 1X2 · O/U gols · O/U escanteios

casa      status     eventos  quotes  tempo  detalhe
--------  ---------  -------  ------  -----  --------------------------------------------------
mock      OK              1       4   0.0s   dados fictícios (teste do pipeline)
betano    FALHOU          0       0   1.2s   HTTP 403 ao carregar
betfair   SEM DADOS       0       0   0.8s   40 evento(s), 0 na competição — relaxe BETO_COMPETITION_INCLUDE
novibet   SEM DADOS       0       0   0.9s   resposta sem JSON reconhecível (bloqueio ou HTML puro)

Coletadas: 1/4 — mock
```

> A coluna **detalhe** é um diagnóstico do funil da coleta (bytes → payloads → eventos →
> em-escopo → mercados → quotes): quando uma casa dá `SEM DADOS`, ela aponta **em qual
> estágio** os eventos se perderam — bloqueio, shape novo do payload, filtro de competição
> restritivo demais ou mercado-alvo não reconhecido — em vez de deixar você no escuro.

### Scan (detecta surebets)

```bash
uv run beto scan
uv run beto scan --min-profit 0.3 --bankroll 5000
```

### Brasileirão via API (RapidAPI)

Além do scraping casa a casa, existe uma fonte **agregada**: a API
[Free API Live Football Data](https://rapidapi.com/Creativesdev/api/free-api-live-football-data)
(RapidAPI). O comando `beto brasileirao` busca **todos os jogos** do Campeonato
Brasileiro nas temporadas pedidas e, para cada um, as **odds pré-jogo 1X2** —
mandante · empate · visitante.

```bash
# 1. assine a API e coloque a chave no .env
echo 'BETO_RAPIDAPI_KEY=sua-chave-aqui' >> .env

# 2. confira as rotas/IDs de liga com a SUA chave (1ª vez, ~10 requests)
uv run beto brasileirao --descobrir

# 3. coleta completa: Série A, 2025 e 2026, exportando CSV e JSON
uv run beto brasileirao --temporadas 2025,2026 --serie a \
    --csv brasileirao.csv --json brasileirao.json
```

Saída:

```
CAMPEONATO BRASILEIRO — série A — temporadas 2025, 2026

série  temp.   rod.  data (BRT)       jogo                          1       X       2
-------------------------------------------------------------------------------------
A      2026    1     09/08/2026 18:00 Flamengo x Palmeiras       1.95    3.55    4.10
...
jogos: 760 | com odds: 380 | pré-jogo (ainda não começaram): 380, destes com odds: 380
requests à API: 763 | cache: 0 acerto(s)
```

Colunas do CSV: `serie, temporada, rodada, data_utc, data_brasilia, mandante, visitante,
status, placar, pre_jogo, odd_mandante, odd_empate, odd_visitante, casa_odds, n_casas,
overround, match_id`. O JSON traz ainda `odds_por_casa` — a cotação 1X2 de **cada** casa
que a API devolveu, que é o que alimenta a detecção de arbitragem
(`CollectReport.to_odds_quotes()` converte tudo para `OddsQuote`).

| Flag | Efeito |
|---|---|
| `--temporadas 2025,2026` | anos a coletar (padrão) |
| `--serie a,b` | Série A e/ou B |
| `--so-futuros` | ignora jogos já iniciados (odds pré-jogo só existem antes do apito) |
| `--sem-odds` | só a tabela de jogos — 1 request por temporada, praticamente não gasta cota |
| `--csv` / `--json` / `--sep` | arquivos de saída e separador do CSV |
| `--sem-cache` | ignora o cache em disco de odds (`BETO_RAPIDAPI_CACHE_DIR`) |
| `--descobrir` | testa as rotas candidatas com sua chave e imprime o que cada uma devolve |

> **Cota.** É uma request por jogo para as odds: uma temporada da Série A (380 jogos)
> consome ~380 chamadas. O plano gratuito da RapidAPI é limitado — por isso as respostas
> de odds ficam em cache em `.cache/rapidapi/` (re-rodar não cobra de novo) e a coleta
> **para sozinha** com aviso quando a cota estoura, em vez de martelar a API. Use
> `--so-futuros` para gastar cota só com o que ainda dá para apostar.

> **Temporadas encerradas.** Odds pré-jogo existem enquanto o jogo não começou. Numa
> temporada já terminada (2025) a API tende a devolver odds vazias ou de fechamento —
> o relatório separa "com odds" de "pré-jogo com odds" justamente para isso ficar
> explícito em vez de parecer bug.

> **Rotas e IDs.** Os nomes de rota e os `leagueid` do Brasileirão (268 = Série A,
> 269 = Série B) seguem a numeração do FotMob e **não foram verificados ao vivo** — o
> ambiente de desenvolvimento não tem acesso à rapidapi.com. Por isso o cliente testa
> uma lista de candidatos e fixa o primeiro que responder com dados úteis; `--descobrir`
> mostra exatamente o que cada rota devolveu com a sua chave, e
> `BETO_RAPIDAPI_MATCHES_PATH` / `BETO_RAPIDAPI_ODDS_PATH` fixam a escolha.

### Loop contínuo

```bash
# sem Telegram — imprime alertas no terminal
BETO_ALERTER=console uv run beto run

# com Telegram
uv run beto run
```

---

## Deploy no Railway (Docker)

O `Dockerfile` já inclui o Chromium do Playwright (`playwright install --with-deps`),
então as casas que dependem de browser funcionam no container. O Railway detecta o
`Dockerfile` automaticamente.

**1. Variáveis** (aba *Variables* — **nunca** commite o token):

```
BETO_ALERTER=telegram
BETO_TELEGRAM_BOT_TOKEN=<token do @BotFather>
BETO_TELEGRAM_CHAT_ID=<seu chat_id>
BETO_ENABLED_HOUSES=betano,sportingbet,novibet,superbet,betfair,betnacional
```

**2. Interface + alertas 24/7 = dois serviços** do mesmo repositório (compartilham as
variáveis acima):

| Serviço | Start command | Função |
|---|---|---|
| **web** | `uv run beto ui` (padrão do Dockerfile) | Interface Streamlit |
| **worker** | `uv run beto run` | Monitor contínuo → Telegram |

No serviço **web**, gere um domínio em *Settings → Networking → Generate Domain*.

**3. Dedup persistente no worker**: anexe um *Volume* montado em `/data` e defina
`BETO_DB_PATH=/data/beto.db` — senão o banco é efêmero e o mesmo alerta repete a cada
reinício.

> **Geo-bloqueio**: as casas `.bet.br` são do mercado regulado brasileiro e costumam
> bloquear IPs estrangeiros/de datacenter. O Railway não tem região no Brasil, então a
> coleta pode falhar com `HTTP 403`/timeout mesmo com o Chromium funcionando. Nesse caso
> configure `BETO_PROXY_SERVER=http://usuario:senha@host:porta` apontando para um **proxy
> residencial brasileiro**, ou rode o coletor numa máquina/conexão no Brasil.

---

## Casas suportadas

| Casa | Estratégia | Status |
|---|---|---|
| **mock** | Dados sintéticos | Sempre funciona (teste de pipeline) |
| **betano** | httpx → endpoint JSON interno (Kaizen) | Implementado |
| **sportingbet** | httpx → CDS API (Entain) | Implementado |
| **novibet** | Playwright + colheitadeira heurística | Implementado |
| **superbet** | Playwright + colheitadeira heurística | Implementado |
| **betfair** | Playwright + colheitadeira heurística | Implementado |
| **betnacional** | Playwright + colheitadeira heurística | Implementado |
| **bet365** | — | Stub — Cloudflare avançado; fase posterior |

> Os endpoints internos de cada casa podem mudar sem aviso e são re-descobertos via
> `beto collect --debug-dump`. O `MockScraper` sempre garante que o restante do pipeline
> (matching, arbitragem, alertas) funcione independentemente dos scrapers reais.

---

## Mercados

| Mercado | MarketType | Linha |
|---|---|---|
| Resultado (1X2) | `MATCH_1X2` | — |
| Over/Under Gols | `OU_GOALS` | 0.5, 1.5, 2.5, … |
| Over/Under Escanteios | `OU_CORNERS` | 8.5, 9.5, … |

A arbitragem em O/U exige a **mesma linha** em casas diferentes (over em uma, under em outra).

---

## Manutenção de scrapers

Cada casa segue o padrão **parser dedicado + fallback heurístico**, tudo diagnosticado:
o adaptador tenta primeiro um parser específico da plataforma (`parse_kaizen` na Betano,
`parse_cds_fixtures` na Sportingbet) e, se ele não render nada, cai na colheitadeira
genérica (`scrapers/harvest.py`) — preenchendo o funil de diagnóstico no caminho.

Quando um scraper parar de coletar, **leia primeiro a coluna `detalhe`** do relatório —
ela diz em qual estágio o funil secou. Depois:

1. Rode `beto collect --houses <casa> --debug-dump` para salvar o payload bruto em `debug/<casa>/`.
2. Cruze o diagnóstico com o dump:
   - *"resposta sem JSON reconhecível"* → bloqueio/geo ou o site mudou para HTML puro (veja proxy BR).
   - *"nenhum evento reconhecido (shape novo?)"* → o formato do payload mudou; ajuste o parser dedicado.
   - *"0 na competição"* → o filtro `BETO_COMPETITION_INCLUDE` está restritivo demais para o momento.
   - *"nenhum mercado-alvo reconhecido"* → adicione palavras-chave em `MARKET_KEYWORDS` (`harvest.py`).
3. Se o shape mudou, ajuste o parser dedicado (padrão de `scrapers/betano.py`/`sportingbet.py`)
   ou as palavras-chave em `harvest.py`.
4. Adicione um fixture em `tests/fixtures/` e um teste em `tests/test_scraper_<casa>.py`.

---

## Testes

```bash
uv run pytest -v
uv run pytest --cov=beto --cov-report=term-missing
```

Todos os testes são **offline** — nenhum acessa a rede. Os scrapers reais são testados contra
fixtures JSON salvas em `tests/fixtures/`.

```bash
uv run ruff check src tests
uv run mypy src
```

---

## Aviso legal

> **Este projeto é para uso pessoal e educacional.**
>
> - O scraping provavelmente **viola os Termos de Uso** das casas de apostas. Use com moderação
>   (rate-limit, delays, baixa frequência) e por sua própria conta e risco.
> - As casas de apostas costumam **limitar ou banir contas** suspeitas de arbitragem.
> - Os alertas são **indicações, não garantias**: odds mudam em segundos e a execução depende de
>   você. Sempre confirme as odds antes de apostar.
> - Não é conselho financeiro. Verifique a legalidade na sua jurisdição.
