# Meewerken aan Podium

## Opzetten

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; op macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Je hebt de Pi niet nodig. Bouw je eigen database door alle podia één keer op
te halen (duurt ~10 minuten), en start dan het dashboard:

```bash
python run_once.py                  # of: python run_once.py --venue paradox
python run_web.py                   # http://127.0.0.1:5011
```

Mail en ntfy staan lokaal uit; zonder instellingen verzamelt de poller alleen.

## Werkwijze

- Werk op een eigen branch en open een pull request naar `main`.
- Push niet rechtstreeks naar `main`. GitHub kan dat op dit abonnement niet
  technisch blokkeren, dus dit is een afspraak.
- Siem merget en deployt. De Pi haalt nooit zelf code op: alles wat op
  `podium.siem.codes` draait, is eerst door Siem bekeken.
- Tests moeten groen zijn. Een bugfix krijgt een test, liefst op een gzipte
  kopie van de echte pagina in `tests/fixtures/`.
- Raakt je PR een scraper, zet dan in de beschrijving hoeveel voorstellingen
  hij vindt vóór en na. Scrapers gaan stil kapot; dat getal is het bewijs.

## Regels

- **Geen geheimen in git**: geen `.env`, wachtwoorden, tokens of database.
  `.env.example` laat zien welke instellingen er zijn; de echte waarden staan
  alleen op de Pi.
- **Wees beleefd tegen de podia**: alle requests gaan via `Fetcher` (pauze
  tussen requests, eerlijke User-Agent). Een wachtrij of botcheck wordt
  herkend en gemeld, maar nooit omzeild — zie "When a scraper breaks" in de
  README.
- **Tijd altijd via `podiumwatch/clock.py`** (Amsterdamse tijd), nooit
  `datetime.now()`: de Pi draait op Londense tijd.
- De README legt per podium uit waarom de code doet wat hij doet. Lees het
  stuk over een podium voordat je zijn adapter aanpast.
