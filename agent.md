# agent.md — Diagnosi & fix: Postgres `integer out of range` + `connection pool exhausted` (Malla / Meshtastic)

## Contesto
Stack Docker (esempi tipici):
- `postgres` (DB `meshtastic_history`)
- `malla-capture` (ingest MQTT → DB)
- `malla-web` (API/UI su `/api/*`, incl. stream pacchetti)

Problemi osservati:
- Errori ripetuti in `malla-capture`/backend DB durante INSERT su `packet_history`
- `malla-web` va *unhealthy* o risponde male / 429 su stream
- Eccezione Python: `psycopg2.pool.PoolError: connection pool exhausted`

---

## Sintomi (cosa si vede)
### 1) INSERT fallisce: `integer out of range`
Nei log compare un `INSERT INTO packet_history ...` con valori che superano il limite `INT4` (32-bit signed).
Valori tipici che sforano:
- `to_node_id = 4294967295` (0xFFFFFFFF, broadcast)
- `mesh_packet_id = 2233462723` (oltre 2,147,483,647)
- `rx_time = 3471739921` (epoch/seconds fuori int32)

**Conseguenza**: Postgres rifiuta l’INSERT → eccezioni continue → più carico → possibili leak/connessioni non rilasciate.

### 2) Pool DB esaurito: `connection pool exhausted`
`malla-web` (o componenti che fanno query) finisce le connessioni del pool psycopg2:
- Query ripetute (dashboard + stream live)
- Possibile mancato `putconn()` in caso di eccezione (leak)
- Molte sessioni in Postgres (es. `idle in transaction`)

### 3) 429 su `/api/stream/packets`
Client (es. browser) fa richieste ripetute/parallele allo stream:
- refresh multipli / più tab aperte
- rate limit applicativo
- backend sotto stress (DB ingest fallito + pool saturo)

---

## Root cause principale
### Schema DB non compatibile con i valori reali
Una o più colonne in `public.packet_history` sono ancora `INTEGER` (int4), ma i dati reali richiedono `BIGINT` (int8).

**Quindi**: prima si sistema lo schema, poi si stabilizza il pool.

---

## Fix immediato (operativo, “stop the bleeding”)

### A) Identifica colonne `integer` in `packet_history`
Esegui:
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name='packet_history'
ORDER BY ordinal_position;"
```

Annota quali sono `integer`. Le più sospette:
- `to_node_id`
- `from_node_id`
- `mesh_packet_id`
- `rx_time`
- (opzionali) `next_hop`, `relay_node`, ecc.

### B) Porta le colonne critiche a `BIGINT`
Esempio (modifica solo quelle che risultano `integer` dalla query sopra):
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -v ON_ERROR_STOP=1 -c "
BEGIN;

ALTER TABLE public.packet_history
  ALTER COLUMN from_node_id   TYPE bigint USING from_node_id::bigint,
  ALTER COLUMN to_node_id     TYPE bigint USING to_node_id::bigint,
  ALTER COLUMN mesh_packet_id TYPE bigint USING mesh_packet_id::bigint,
  ALTER COLUMN rx_time        TYPE bigint USING rx_time::bigint;

COMMIT;"
```

> Nota: l’ALTER può fare lock della tabella per un breve periodo.

### C) Ripulisci connessioni “appese” e resetta il web pool
1) Controlla stato connessioni:
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT state, count(*)
FROM pg_stat_activity
WHERE datname='meshtastic_history'
GROUP BY state
ORDER BY count(*) DESC;"
```

2) Vedi chi le tiene:
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT pid, client_addr, application_name, state,
       now()-xact_start AS xact_age,
       now()-state_change AS state_age,
       query
FROM pg_stat_activity
WHERE datname='meshtastic_history'
ORDER BY xact_start NULLS LAST;"
```

3) Termina `idle in transaction` (se presenti):
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname='meshtastic_history'
  AND pid <> pg_backend_pid()
  AND state IN ('idle in transaction');"
```

4) Restart `malla-web` per resettare il pool:
```bash
docker compose restart malla-web
```

---

## Verifica post-fix
### 1) Nessun `integer out of range`
Controlla i log:
```bash
docker compose logs -f malla-capture | grep -i "out of range" -n
docker compose logs -f malla-web     | grep -i "PoolError" -n
```

### 2) Insert effettivi (DB che cresce)
Esempio:
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
SELECT count(*) AS rows, max(timestamp) AS latest
FROM public.packet_history;"
```

### 3) UI/stream stabile
Apri una sola tab, niente refresh compulsivo; osserva se spariscono i 429.

---

## Hardening (dopo stabilizzazione)
### A) Timeout per transazioni “dimenticate”
Imposta a livello DB:
```bash
docker compose exec -T postgres psql -U postgres -d meshtastic_history -c "
ALTER DATABASE meshtastic_history
SET idle_in_transaction_session_timeout = '60s';"
```
(Valuta 30–120s in base al carico.)

### B) Indagine su leak del pool in codice
Cerca nel repo l’uso del pool psycopg2:
```bash
grep -RIn "ThreadedConnectionPool\|SimpleConnectionPool\|getconn\|putconn" .
```

Regola d’oro:
- `conn = pool.getconn()`
- **sempre** `pool.putconn(conn)` in `finally`, anche in caso di eccezione

Esempio pattern:
```python
conn = pool.getconn()
try:
    # query...
    pass
finally:
    pool.putconn(conn)
```

### C) Stream / polling
Se il front-end/stream fa richieste parallele:
- ridurre polling, usare 1 sola connessione stream
- introdurre backoff (exponential backoff)
- rate limit più intelligente lato server (o disabilitare se è eccessivo)

---

## Note utili
- `to_node_id = 0xFFFFFFFF` è normale (broadcast) e *deve essere supportato*.
- `mesh_packet_id` e timestamp possono superare int32; usare `BIGINT` evita futuri crash.
- Se hai molte istanze (World/IT/EU/Toscana), ripeti la verifica schema per ogni DB/stack.

---

## Checklist finale (per Codex)
1. Eseguire query `information_schema` e individuare colonne `integer` in `packet_history`
2. Alterare a `BIGINT` le colonne che possono sforare (minimo: `to_node_id`, `mesh_packet_id`, `rx_time`, spesso anche `from_node_id`)
3. Terminare sessioni `idle in transaction` se presenti
4. Restart `malla-web`
5. Verificare log: niente più `out of range` e niente più `PoolError`
6. (Opzionale) impostare `idle_in_transaction_session_timeout`
7. (Opzionale) fix codice per `putconn()` garantito + tuning stream/polling
