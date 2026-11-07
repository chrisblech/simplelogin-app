# Upstream-Upgrades mit `sync-deploy.sh`

Erfahrungswerte aus bisherigen Upgrades dieses Forks auf ein neues
SimpleLogin-Release. Ergänzt `scripts/sync-deploy.sh` (nur im Branch `build`).

## Kurzfassung

```sh
scripts/upgrade-preflight.sh --test     # Probelauf: Merges, Checks, Migrationen, Tests
# ... gemeldete Probleme in den Feature-Branches lösen und pushen,
#     ggf. vorher lokal prüfen mit: scripts/upgrade-preflight.sh --local --test
scripts/sync-deploy.sh                  # echter Lauf: Merge, Checks, Push, Image
# Image-Tag vX.Y.Z-cb in Portainer setzen
```

## Werkzeuge

| Datei | Zweck |
|-------|-------|
| `scripts/sync-deploy.sh` | Synchronisiert `master`, baut `deploy/<tag>-cb` aus `DEPLOY_BRANCHES`, prüft, pusht, baut + pusht das Image. Einzige Quelle für die Branch-Liste. |
| `scripts/upgrade-preflight.sh` | Probelauf in einem Wegwerf-Worktree. Ändert keine Branches, pusht nichts. Meldet **alle** Probleme auf einmal: Konflikte je Branch (mit Upstream bzw. zwischen eigenen Branches), uv.lock, Enum-Kollisionen, Alembic-Heads, ruff, neue Migrationen/Abhängigkeiten; mit `--test` zusätzlich Alembic-Migration + pytest gegen Postgres 17.7 + Redis. `--local` nutzt die lokalen statt der `origin`-Branches, `--keep` behält den Worktree. |
| `scripts/upgrade_checks.py` | Gemeinsame Checks (nur stdlib): `dedupe-deps`, `alembic-heads`, `enum-dupes [REF]`. |

`sync-deploy.sh` erledigt automatisch:
- Konflikte, die **nur `uv.lock`** betreffen: eigene Seite nehmen + `uv lock`.
- Doppelte Einträge in `pyproject.toml` entfernen + neu locken (Sicherheitsnetz).
- **Vor dem Push** wird abgebrochen bei: inkonsistentem `uv.lock`, neuen
  doppelten Enum-Werten (gegenüber dem Upstream-Tag), mehr als einem
  Alembic-Head (außer `ALLOW_MULTIPLE_ALEMBIC_HEADS=1`).
- Bei echten Konflikten bricht es mit Hinweis ab. **Nie im Deploy-Branch
  lösen**: Er wird bei jedem Lauf per `git checkout -B` neu aufgebaut, die
  Lösung wäre beim nächsten Mal weg.

## Konflikte lösen

Das Release in den betroffenen Feature-Branch mergen
(`Merge upstream vX.Y.Z into <branch>`), dort lösen, testen, pushen.
Konflikte *zwischen* eigenen Branches (Preflight: „conflicts with an earlier
deploy branch“) so lösen, dass die Branches sich nicht mehr in denselben
Zeilen berühren (Beispiel: Imports, siehe unten).

## Strukturelle Regeln (damit Konflikte gar nicht erst entstehen)

### Enum-Werte des Forks ab 1000
Upstream nummeriert z. B. `AuditLogActionEnum` fortlaufend. Ein gleicher Wert
an *anderer* Stelle erzeugt **keinen** Git-Konflikt, sondern Python macht
still einen Alias daraus (falsche Audit-Logs). Deshalb:
- Eigene Enum-Werte immer im Bereich **1000+** vergeben.
- `upgrade_checks.py enum-dupes <tag>` prüft das (in beiden Skripten aktiv);
  Upstreams eigene, beabsichtigte Aliase werden ignoriert.

Historie: bis v4.83.1 war `upgrade_lifetime = 20`; v4.83.5 belegt 20 mit
`delete_mailbox`. Seit v4.83.5 ist `upgrade_lifetime = 1000`. Einmalig beim
Deploy von v4.83.5-cb (bevor jemand über das Admin-Panel eine Mailbox löscht):
```sql
UPDATE admin_audit_log SET action = 1000 WHERE action = 20;
```

### Abhängigkeiten nur in `fix/uv-lock-protobuf-conflict`
Nur dieser Branch ändert `pyproject.toml`/`uv.lock` (protobuf 5.x,
cachetools, google-api-client). Andere Feature-Branches fügen keine eigenen
Deklarationen hinzu, wenn das Paket schon transitiv vorhanden ist (so macht
es Upstream mit `cachetools` auch). Bei `uv.lock`-Konflikt in diesem Branch:
`git checkout --ours uv.lock && uv lock`, prüfen, dass protobuf 5.x bleibt.
Dieser Branch wäre ein guter Kandidat für einen Upstream-PR (Issue #2797);
nach Übernahme durch Upstream aus `DEPLOY_BRANCHES` entfernen.

### Imports in `app/admin/user.py`
`feature/admin-create-user` sortiert den `from app.models import (...)`-Block
um. Andere Branches ändern diesen Block nicht, sondern importieren in einer
**eigenen Zeile davor** (`from app.models import PartnerSubscription, PartnerUser`)
und loggen über `AdminAuditLog`-Classmethods (`upgrade_lifetime`,
`delete_user`), damit `AuditLogActionEnum` nicht doppelt importiert wird
(ruff F811).

### Alembic: mehrere Heads
`feature/user-blacklists` hat eigene Migrationen. Mit jedem Upstream-Release,
das neue Migrationen mitbringt, entsteht ein zweiter Head. Prod führt
Migrationen im Container `sl-migration` mit `alembic upgrade head` (Singular)
aus; bei zwei Heads schlägt das fehl.

- **Empfohlen (einmalig):** im Portainer-Stack beim `sl-migration`-Command
  `alembic upgrade head` → `alembic upgrade heads` ändern und in
  `sync-deploy.sh` `ALLOW_MULTIPLE_ALEMBIC_HEADS=1` setzen. Dann braucht es
  keine Merge-Migrationen mehr. `upgrade heads` funktioniert auch bei nur
  einem Head, die Umstellung ist also jederzeit gefahrlos.
- **Bis dahin:** im Blacklist-Branch eine leere Merge-Migration mit beiden
  Heads als `down_revision` anlegen (Vorlage:
  `*_merge_partner_user_indices_with_sender_blacklist.py`). Merge-Migrationen
  dürfen nicht automatisch im Deploy-Branch erzeugt werden: Ihre Revision
  landet in `alembic_version` und muss im nächsten Deploy weiter existieren.

### Bereits upstream gemergte Branches
Wenn ein eigener Fix upstream übernommen wurde (z. B. `fix_oidc_scopes` in
v4.82.3), den Branch aus `DEPLOY_BRANCHES` entfernen.

## Sonstiges

- Tests brauchen seit v4.83.x zusätzlich **Redis** auf `localhost:6379`
  (`scripts/run-test.sh` startet nur Postgres); der Preflight startet beides.
- Bekannte, bereits bestehende `ruff format`-Abweichungen aus dem
  Blacklist-Branch: `app/regex_utils.py`, `app/user_audit_log_utils.py`.
- Neue Upstream-Migrationen vor dem Deploy ansehen (Extensions wie
  `pg_trgm`, `CREATE INDEX CONCURRENTLY`, Datenmigrationen). Der Preflight
  listet sie gegenüber dem zuletzt deployten Release auf.
- Sender-Blacklist-Muster werden per `fullmatch` geprüft: `.*@spam\.test`,
  nicht `@spam\.test$`.

## Verlauf

| Release  | Besonderheiten |
|----------|----------------|
| v4.82.3  | `fix_oidc_scopes` upstream gemergt, aus Liste entfernt |
| v4.83.1  | Alembic-Merge (FIDO/Blacklist, Email-Canonical-Indizes); Import-Konflikt admin-create-user manuell im Deploy-Branch |
| v4.83.5  | Alembic-Merge (partner_user-Indizes); Enum-Kollision 20 → `upgrade_lifetime = 1000` + Daten-Update; Import-Konflikt dauerhaft im Bulk-Branch gelöst; Blacklist-Branch ohne eigene Dependency; `prometheus-client` neu; Tests brauchen Redis; Preflight-Skript + Checks in sync-deploy eingeführt |
