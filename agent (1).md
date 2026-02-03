# agent.md — Malla (worldmap) “Database error” con container *healthy*

## Contesto
Deploy Docker Compose `world_malla` (servizi: `malla-web`, `malla-capture`, `postgres`).  
La web risponde ma la UI/API restituisce:

```json
{"error":"Database error","message":"An internal error occurred"}
```

I container risultano *healthy* in `docker compose ps`, ma l’app mostra errore DB.

---

## Sintomi osservati
- `malla-web` logga errori tipo:
  - `Failed to connect to database after 3 attempts: connection pool exhausted`
  - `Failed to initialize live stream cursor`
- Postgres è up e contiene dati (es. `packet_history` con centinaia di migliaia di righe).
- `pg_stat_activity` mostra molte sessioni `idle in transaction` dal container `malla-web`.

---

## Evidenze / Diagnosi (root cause)
Il pool connessioni del web viene **esaurito** perché `malla-web` apre transazioni e le lascia **aperte** (stato PostgreSQL: `idle in transaction`) per molto tempo.

Esempio reale:
- `MALLA_DB_POOL_MAX=60`
- `pg_stat_activity`:
  - `idle in transaction = 60` (client_addr = IP del container `malla-web`)
- Le query “appese” sono spesso `SELECT ... FROM packet_history ...` (tipicamente endpoint live/stream/polling).

**Effetto:** quando arrivano nuove richieste, non c’è più alcuna connessione disponibile nel pool → l’app ritorna “Database error”.

> Nota: *healthy* ≠ “query funzionano”. L’healthcheck spesso verifica solo `/health` o la reachability del processo, non la bontà delle query applicative.

---

## Come verificare velocemente
### 1) Variabili DB nel container web
```bash
docker compose exec -T malla-web sh -lc 'env | grep -Ei "DATABASE|DB_|POSTGRES|PGHOST|PGPORT|PGUSER|PGDATABASE|PGPASSWORD" | sort'
```

### 2) Stato connessioni PostgreSQL
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "SHOW max_connections;"

docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT state, count(*)
FROM pg_stat_activity
WHERE datname='meshtastic_history'
GROUP BY state
ORDER BY count(*) DESC;"
```

### 3) Identificare il client che “satura”
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT client_addr, application_name, state, count(*)
FROM pg_stat_activity
WHERE datname='meshtastic_history'
GROUP BY 1,2,3
ORDER BY 4 DESC;"
```

### 4) Vedere le query bloccate/vecchie
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT pid, client_addr, state,
       now()-query_start AS age,
       left(query,120) AS q
FROM pg_stat_activity
WHERE datname='meshtastic_history'
  AND query_start IS NOT NULL
ORDER BY age DESC
LIMIT 20;"
```

---

## Risoluzione rapida (ripristino servizio)
### A) Terminare le sessioni “idle in transaction” dal web
Sostituire `172.22.0.4` con l’IP reale del container `malla-web` se diverso:

```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname='meshtastic_history'
  AND client_addr='172.22.0.4'
  AND state='idle in transaction';
"
```

### B) Riavviare solo la web
```bash
docker compose restart malla-web
```

### C) Verifica post-fix
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT state, count(*)
FROM pg_stat_activity
WHERE datname='meshtastic_history'
GROUP BY state
ORDER BY count(*) DESC;"
```

---

## Mitigazioni permanenti (consigliate in produzione)
### 1) Timeout automatico per “idle in transaction”
Impedisce che una transazione resti appesa per ore e protegge il sistema anche se il bug ricapita.

Esempio (120s):

```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
ALTER DATABASE meshtastic_history
SET idle_in_transaction_session_timeout = '120s';
"
docker compose restart malla-web
```

> Opzionale (più aggressivo): 60s.

### 2) Ridurre il pool del web
Se l’app ha un leakage, un pool enorme può diventare “un buco nero” che congela tutto.  
Esempio nel `docker-compose.yml` del `malla-web`:

```yaml
environment:
  MALLA_DB_POOL_MAX: "15"
```

---

## Fix definitivo (lato codice applicativo)
Il bug è quasi certamente in una rotta “live” (SSE/stream/polling) che:
- apre una transazione / cursor,
- fa query,
- **non** fa `commit/rollback`,
- **non** chiude cursor/connessione in `finally`.

### Obiettivo tecnico
- Evitare di mantenere una **connessione DB aperta** per tutta la durata dello stream.
- Ogni iterazione deve fare: `open → query → close` (connessione rilasciata subito).
- In caso di eccezioni: `finally` sempre.

### Pattern consigliati
- **Autocommit** per query read-only.
- Context manager / `try/finally` per garantire `close()`.

Pseudo-esempio (concetto):
```python
# concetto: ogni giro prende una connessione, fa query, chiude
while True:
    with db.connect() as conn:
        rows = conn.execute("SELECT ...")
    yield format(rows)
    time.sleep(1)
```

Se usi SQLAlchemy:
- evitare sessioni globali non chiuse,
- `session.close()` sempre,
- niente generator che esce senza cleanup.

---

## Checklist rapida post-fix
- [ ] `pg_stat_activity`: `idle in transaction` vicino a 0
- [ ] mappa/caricamento UI ok
- [ ] `malla-web` log non contiene più `connection pool exhausted`
- [ ] timeout DB impostato (`SHOW idle_in_transaction_session_timeout;` a livello sessione o `ALTER DATABASE` applicato)
- [ ] pool max ridotto (se necessario) e monitorato

---

## Note operative
- Il problema può riapparire se c’è traffico esterno (worldmap pubblica) e lo stream riconnette spesso.
- La mitigazione con `idle_in_transaction_session_timeout` è il “salvagente” più efficace per mantenere il servizio disponibile anche prima della patch codice.
