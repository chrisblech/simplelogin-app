# Upstream-Upgrades mit `sync-deploy.sh`

Erfahrungswerte aus bisherigen Upgrades dieses Forks auf ein neues
SimpleLogin-Release. Ergänzt `scripts/sync-deploy.sh` (nur im Branch `build`).

## Ablauf

1. **Neues Release ermitteln**
   ```sh
   git fetch upstream --tags --prune
   git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged upstream/master --sort=-v:refname | head
   ```
   (Alte Tags wie `v20220512.02` ignoriert das Skript per Regex.)

2. **Probe-Merge in einem Wegwerf-Worktree**, bevor das Skript läuft:
   ```sh
   git worktree add --detach /tmp/wt vX.Y.Z
   cd /tmp/wt
   for b in <DEPLOY_BRANCHES in Skript-Reihenfolge>; do
     git merge --no-ff --no-edit origin/$b || { git status --short; git merge --abort; }
   done
   ```
   Wichtig: das Skript baut den Deploy-Branch bei jedem Lauf per
   `git checkout -B` neu auf. **Konflikte, die nur im Deploy-Branch gelöst
   werden, gehen beim nächsten Lauf verloren.**

3. **Konflikte im Feature-Branch lösen**: das Release in den betroffenen
   Feature-Branch mergen (`Merge upstream vX.Y.Z into <branch>`), dort lösen,
   Feature-Branch pushen. Danach den Probe-Merge wiederholen, bis alle
   Branches *in Skript-Reihenfolge* sauber durchlaufen – Konflikte können
   auch *zwischen* Feature-Branches entstehen, nicht nur mit Upstream.

4. **Prüfen** (auf dem simulierten Deploy-Stand, inkl. Dedupe des
   doppelten `cachetools`-Eintrags + `uv lock`, wie im Skript):
   - Alembic hat genau **einen** Head (siehe unten).
   - `uv lock --check` ok, `protobuf` bleibt 5.x.
   - `uvx ruff@0.1.5 check .` (Version wie in `.pre-commit-config.yaml`).
     Bekannte, bereits bestehende `ruff format`-Abweichungen:
     `app/regex_utils.py`, `app/user_audit_log_utils.py`.
   - Tests wie in `scripts/run-test.sh`, aber Postgres in Prod-Version und
     **zusätzlich Redis** (seit v4.83.x brauchen Tests `localhost:6379`):
     ```sh
     docker run -d --name sl-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test \
       -e POSTGRES_DB=test -p 127.0.0.1:15432:5432 postgres:17.7
     docker run -d --name sl-test-redis -p 127.0.0.1:6379:6379 redis:7.4-alpine
     uv sync --locked
     CONFIG=tests/test.env uv run alembic upgrade head
     uv run pytest -c pytest.ci.ini
     docker rm -f sl-test-db sl-test-redis
     ```

5. **Skript laufen lassen**: `scripts/sync-deploy.sh` (aus `build`).
   Danach Image-Tag `vX.Y.Z-cb` in Portainer setzen.

6. **Neue Upstream-Migrationen** vorher ansehen (z. B. benötigte
   Postgres-Extensions wie `pg_trgm`, `CREATE INDEX CONCURRENTLY`).

## Wiederkehrende Stolperstellen

### Alembic: mehrere Heads (`feature/user-blacklists`)
Der Blacklist-Branch bringt eigene Migrationen mit. Sobald Upstream neue
Migrationen hat, gibt es zwei Heads. Lösung: im Blacklist-Branch eine leere
Merge-Migration mit beiden Heads als `down_revision` anlegen (Vorlage:
`*_merge_partner_user_indices_with_sender_blacklist.py`). Heads prüfen:

```python
import re, glob
revs, downs = {}, set()
for f in glob.glob("migrations/versions/*.py"):
    s = open(f).read()
    revs[re.search(r"^revision\s*=\s*['\"](\w+)", s, re.M).group(1)] = f
    downs |= set(re.findall(r"['\"](\w+)['\"]", re.search(r"^down_revision\s*=\s*(.+)$", s, re.M).group(1)))
print([(r, f) for r, f in revs.items() if r not in downs])
```

### `uv.lock`
- `feature/user-blacklists` deklariert `cachetools ~= 5.3.3`, sein `uv.lock`
  ist absichtlich nicht aktuell. Das Skript entfernt den doppelten Eintrag und
  lockt neu – dort also **nicht** `uv lock` ausführen (würde protobuf auf
  3.20 zurückdrehen).
- `fix/uv-lock-protobuf-conflict`: bei `uv.lock`-Konflikt
  `git checkout --ours uv.lock && uv lock`, dann prüfen, dass nur die
  gewollten Pins plus neue Upstream-Pakete im Diff sind.

### `AuditLogActionEnum`-Kollisionen (`feature/admin-user-bulk-delete-lifetime`)
Der Branch ergänzt Enum-Werte, Upstream ebenfalls. Werte werden als Integer
in `admin_audit_log.action` gespeichert. Bei Kollision bekommt Upstream den
Wert, unserer wird hochgezählt – **und bestehende Prod-Zeilen müssen
umnummeriert werden**, sonst werden sie falsch angezeigt.

- v4.83.5: Upstream `delete_mailbox = 20`, unser `upgrade_lifetime` → 21.
  Einmalig beim Deploy (vor der ersten Mailbox-Löschung über das Admin-Panel):
  ```sql
  UPDATE admin_audit_log SET action = 21 WHERE action = 20;
  ```

### `app/admin/user.py`-Importe (Bulk-Branch vs. `feature/admin-create-user`)
`admin-create-user` sortiert den `from app.models import (...)`-Block um.
Der Bulk-Branch importiert seine zusätzlichen Modelle deshalb in einer
**separaten Zeile vor dem Block** und lässt den Upstream-Block unverändert;
Audit-Logging läuft über `AdminAuditLog`-Classmethods (`upgrade_lifetime`,
`delete_user`), damit `AuditLogActionEnum` nicht doppelt importiert wird
(ruff F811). Seit v4.83.5 so umgesetzt; bis v4.83.1 wurde dieser Konflikt
jedes Mal von Hand im Deploy-Branch gelöst.

### Bereits upstream gemergte Branches
Wenn ein eigener Fix upstream übernommen wurde (z. B. `fix_oidc_scopes` in
v4.82.3), den Branch aus `DEPLOY_BRANCHES` entfernen.

## Verlauf

| Release  | Besonderheiten |
|----------|----------------|
| v4.82.3  | `fix_oidc_scopes` upstream gemergt, aus Liste entfernt |
| v4.83.1  | Alembic-Merge (FIDO/Blacklist, Email-Canonical-Indizes); Import-Konflikt admin-create-user manuell im Deploy-Branch |
| v4.83.5  | Alembic-Merge (partner_user-Indizes); Enum-Kollision 20 → `upgrade_lifetime = 21` + Daten-Update; Import-Konflikt dauerhaft im Bulk-Branch gelöst; `prometheus-client` neu; Tests brauchen Redis |
