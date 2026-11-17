Англійська версія: [M07-T02-run-keycloak-locally.md](M07-T02-run-keycloak-locally.md)

# M07-T02 — Запустіть Keycloak локально

Задача: [M07-T02 — Run Keycloak locally](../curriculum/milestones/M07-identity-and-authorization/M07-T02-run-keycloak-locally.md)

## Що ви зробите

Цей урок дає проєкту власного постачальника ідентичності: локальний Keycloak, який знає кількох демо-користувачів, пускає їх увійти і видає підписані токени. Токен каже, хто людина і в яких вона групах. Сьогодні жоден маршрут не перевіряє, хто звертається; з M07-T03 API перевірятиме саме ці токени.

- **Закріплено:** одна точна версія Keycloak, зафіксована digest образу, тож на кожній машині працює той самий сервер.
- **Realm:** окремий простір у Keycloak для цього проєкту. У ньому один клієнт (Open WebUI), одна точна адреса повернення після входу, ключі підпису, які Keycloak генерує сам, дві групи, чотири ролі застосунку і три демо-користувачі.
- **Жодних облікових даних у репозиторії:** кожен пароль і секрет генерується на вашій машині у файл, який Git ігнорує. Файл realm містить лише заповнювачі.
- **Відтворюваність:** `make keycloak-reset` і `make keycloak-up` дають свіжий Keycloak із порожнього тому, і ті самі тести знову входять.

| Задача просить | Де це відбувається |
|---|---|
| Закріплена локальна конфігурація Keycloak | Крок 3: `deploy/keycloak/compose.yaml` |
| Realm, клієнти, redirect URI, ключі підпису, демо-користувачі й групи | Крок 4: `deploy/keycloak/knowledge-service-realm.json` |
| Жодних облікових даних у репозиторії | Крок 2: `scripts/keycloak_env.py` і ігнорований `deploy/keycloak/.env` |
| Тести: успіх, найризикованіший край, типізована відмова | Крок 6: `tests/test_keycloak_config.py`, `tests/test_keycloak_integration.py` |
| Чистий том відтворює вхід і видачу токенів | Крок 7: скинути, запустити, перевірити знову |
| Оновити лише те, що зміна зробила неточним | Крок 8: `README.md` і `docs/threat-model.md` |

Цей урок містить код: кроки 2, 3, 4 і 6 пишуть код або конфігурацію. Кроки 1, 5, 7 і 8 — ні, хоча крок 5 запускає Keycloak, а крок 8 редагує два документи.

Терміни цього уроку:

- **OIDC (OpenID Connect):** стандартний спосіб для застосунку запитати в постачальника ідентичності «хто ця людина?» і отримати підписану відповідь.
- **Realm:** окремий світ усередині Keycloak зі своїми користувачами, групами, клієнтами і ключами, як окремий орендар.
- **Клієнт** (client): застосунок, якому дозволено надсилати користувачів у Keycloak на вхід, тут Open WebUI.
- **Redirect URI:** точна адреса, куди Keycloak повертає користувача після входу разом з одноразовим кодом. Якби нападник міг її вибрати, код дістався б йому.
- **Authorization code flow:** вхід через браузер: користувач вводить пароль на сторінці Keycloak, а не в застосунку, і застосунок обмінює повернений код на токени.
- **PKCE:** одноразовий секрет, який застосунок створює перед входом і доводить під час обміну коду, тож украдений код сам по собі марний. `S256` надсилає лише його хеш.
- **Access token / ID token:** підписаний JSON (JWT). Access token — для API; ID token — для клієнта.
- **Claim:** одне поле в токені, наприклад `sub` (ID користувача), `groups`, `aud` (для кого токен) або `iss` (хто його видав).
- **Scope:** іменований набір claims, який клієнт може попросити під час входу.
- **JWKS:** публічні ключі, які публікує Keycloak, кожен зі своїм ID (`kid`), щоб будь-хто міг перевірити підпис токена.

## Перш ніж почати

Цей розділ пояснює рішення в конфігурації, щоб кожен крок мав сенс, коли ви до нього дійдете.

- **Compose в окремій папці.** `deploy/keycloak/` містить файл Compose і файл realm. M12-T03 пізніше збере всю систему; окремий файл зараз дає змогу запускати Keycloak самостійно. Makefile отримує `keycloak-up`, `keycloak-down` і `keycloak-reset`.
- **`start-dev` з томом.** Режим розробки тримає дані у вбудованій базі всередині тому, без TLS і без зовнішньої бази. Це правильно для ноутбука і неправильно для продакшну, тому realm і README кажуть «non-production».
- **Секрети через заповнювачі.** Під час імпорту Keycloak замінює `${NAME}` у файлі realm змінною середовища `NAME`. Compose передає кожну змінну як `${NAME:?...}`, що відмовляється стартувати без неї, тож Keycloak ніколи не імпортує буквальне `${...}` як пароль.
- **Імпорт лише раз.** Keycloak імпортує realm, лише коли його ще немає, тож зміна файлу realm потребує `make keycloak-reset`. Через те саме правило нові секрети поруч зі старим томом не збігалися б зі збереженими паролями, тому `make keycloak-up` відмовляється їх створювати, доки том існує.
- **Один клієнт, лише вхід через браузер.** `open-webui` — конфіденційний клієнт: code flow з PKCE S256 і один точний redirect URI, `http://localhost:3000/oauth/oidc/callback`. Implicit flow, вхід лише за паролем і сервісні акаунти вимкнені. Його токени містять аудиторію `knowledge-service-api`, групи користувача повним шляхом (`/engineering`) і лише чотири ролі застосунку.
- **Ключі підпису робить Keycloak.** Realm називає постачальників ключів (RSA для підписів `RS256`, плюс HMAC і AES, які Keycloak використовує всередині), але не сам ключ. Keycloak генерує їх під час першого запуску і тримає в томі; скидання дає нові. Тому M07-T03 має читати ключі з JWKS, а не з файлу.
- **Демо-користувачі з фіксованими ID.** `alice` (`/engineering`, `user`), `bob` (`/finance`, `user`, `content-manager`), `carol` (без групи, `user`, `administrator`). Їхній `sub` не змінюється між скиданнями, тож пізніші тести можуть на нього покладатися. Carol показує, що роль застосунку ніколи не дає доступу до Документів.
- **Тести на двох рівнях.** Unit-тести читають файли: закріплення, точні redirect URI, заповнювачі і скрипт секретів. Інтеграційні тести входять через справжню форму входу за допомогою `tests/keycloak_flow.py`, яким скористається M07-T03. Вони пропускають себе, коли Keycloak не запущено.
- **Що знайшло ревʼю.** Незалежне адверсарне ревʼю спробувало понад двадцять варіантів redirect URI, інші response types і modes, plain і відсутній PKCE, повторне використання коду, token exchange, device і backchannel grants, реєстрацію клієнтів і admin API. Жодного коду чи токена через `open-webui` без пароля, секрету і PKCE verifier воно не отримало. Знайдено такі проблеми, усі виправлено; кожна проблема конфігурації має тест, що падає на першій версії:
  - Запит scope `microprofile-jwt` клав назви ролей realm у claim `groups`, тож користувач міг видати `administrator` за групу. Тепер клієнт не має необовʼязкових scopes, має фіксовані scopes за замовчуванням і лише ролі застосунку в токенах.
  - Вбудований клієнт `admin-cli` у realm видавав токен за одним лише паролем. Тепер він вимкнений, і тест пробує кожен вбудований клієнт.
  - `make keycloak-reset` падав, коли бракувало `.env`, а `make keycloak-up` потім створював секрети, яких збережений realm не використовував. Тепер скидання працює за назвою проєкту, а запуск відмовляється створювати нові секрети поруч зі старим томом.
  - Помічник входу однаково повідомляв про неправильний пароль, заблокованого користувача і застарілі секрети. Тепер він передає власну причину Keycloak, і тести її перевіряють.
  - Деякі тести проходили на зламаній конфігурації: рядок порту без лапок, метод PKCE `plain`, Keycloak без realm, показаний як пропущений. Тепер кожне перевіряється.
  - Кожне скидання давало демо-користувачам нові ID, а короткі назви груп могли збігтися. Тепер ID фіксовані, а групи — з повним шляхом.
  - Задокументовано, не виправлено: Keycloak ненадовго блокує користувача після швидких невдалих входів; digest образу перевірено вручну, а не тестом; відредагований `.env` лишається непоміченим, доки не впаде вхід; issuer `http://localhost:8180` недосяжний зсередини контейнера (M09-T08, M12-T03).
- **Відомі обмеження.** Це лише для локальної роботи: без TLS, вбудована база, демо-користувачі. API поки не перевіряє жодного токена; це M07-T03.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і передбачення

Перш ніж щось додавати, запишіть, що має бути правдою наприкінці уроку. Крок 7 перевірить передбачення.

Створіть `docs/evidence/M07-T02-run-keycloak-locally.md`:

```markdown
# M07-T02 — Run Keycloak locally evidence

## Invariant

`make keycloak-up` starts a pinned Keycloak on 127.0.0.1 with the
`knowledge-service` realm: one client, `open-webui`, using the code flow
with S256 PKCE and one exact redirect URI; signing keys Keycloak generates;
two groups, four application roles, and three non-production demo users.
Every password and secret is generated into a Git-ignored file; the
repository holds none. From an empty volume, a demo user logs in through
the login form and gets an RS256 access token for `knowledge-service-api`
naming their groups by full path and only their application roles.

## Expected failing check

Before the change, `make keycloak-up` fails with `No rule to make target`
for `keycloak-up`.

## Predictions

- What Keycloak does with a realm file that changed while its volume already holds the realm: ...
- What a login gets when the redirect URI differs from the registered one by one character: ...
- Where the signing key's private half lives, and what happens to it on `make keycloak-reset`: ...
- How many integration tests fail if the client allows the optional `microprofile-jwt` scope again, before and after a reset: ...

## Design

## Verification

## Review

## Decision

## Reflection
```

Перевірка: файл існує, перевірка падає, і кожне передбачення — повне речення, яке крок 7 може спростувати.

### Крок 2 — Крок із кодом: згенеруйте секрети і цілі make

Цей крок створює єдине місце, де живуть секрети, ще до того, як вони знадобляться будь-якому файлу.

**Простими словами:** Невеликий скрипт записує пʼять випадкових секретів у файл поруч із файлом Compose: пароль адміністратора Keycloak, секрет клієнта Open WebUI і паролі трьох демо-користувачів. Читати файл можете лише ви, Git його ігнорує, а скрипт ніколи не друкує значення. Під час повторного запуску він лишає файл як є, бо realm, уже збережений у Keycloak, використовує саме ці значення. Цілі make запускають скрипт, запускають чи зупиняють Keycloak і все скидають.

| Частина | Що робить |
|---|---|
| `NAMES` | Пʼять секретів; крок 6 перевіряє, що вони точно збігаються з файлом Compose і realm |
| `create` | Відкриває файл з `O_EXCL` і правами `0600`: ніколи не замінює файл, читати може лише власник |
| `main` | Створює відсутній файл, лишає повний, відмовляє файлу, якому бракує назви (код 1) |
| `keycloak-up` | Відмовляє, коли `.env` зник, а том ні; далі скрипт, далі `docker compose up --wait` |
| `keycloak-reset` | Видаляє контейнер, том і `.env` за назвою проєкту, тож працює і без `.env` |

Створіть `scripts/keycloak_env.py`:

```python
"""Create the local Keycloak's secrets once, in a file Git ignores.

`make keycloak-up` runs this before starting Keycloak. It writes
`deploy/keycloak/.env` with a fresh random value for every secret the
Compose file and the realm need, readable by the owner only, and prints the
file's name but never a value. An existing file is kept as it is, because
the realm imported into Keycloak's volume already uses its values; a file
that lacks a secret is refused, and `make keycloak-reset` starts over.
"""

import os
import secrets
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "deploy/keycloak/.env"
NAMES = (
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
    "KS_OPEN_WEBUI_CLIENT_SECRET",
    "KS_ALICE_PASSWORD",
    "KS_BOB_PASSWORD",
    "KS_CAROL_PASSWORD",
)


def existing_names(path: Path) -> set[str]:
    """The names an existing file sets, without reading any value out."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split("=", 1)[0] for line in lines if "=" in line}


def create(path: Path) -> None:
    """Write a new file with a random value per name, owner-readable only."""
    lines = [f"{name}={secrets.token_urlsafe(24)}\n" for name in NAMES]
    # O_EXCL: never replace a file another run created in the meantime.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.writelines(
            ["# Local Keycloak secrets; generated, never commit.\n", *lines]
        )


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    if not path.exists():
        create(path)
        sys.stdout.write(f"created {path.name} with {len(NAMES)} secrets\n")
        return 0
    missing = [name for name in NAMES if name not in existing_names(path)]
    if missing:
        sys.stderr.write(
            f"{path.name} lacks {', '.join(missing)}; "
            "run `make keycloak-reset` to start over\n"
        )
        return 1
    sys.stdout.write(f"kept the existing {path.name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

У `Makefile`:

**1.** Знайдіть:

```make
# targets keep working exactly as before (CI and pre-commit call them directly),
# and a bare `make` opens the same menu.

# Bare `make` shows the command menu.
.DEFAULT_GOAL := do_good

.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check check hooks-install hooks docs-check

# ---------------------------------------------------------------------------
# Command menu
# ---------------------------------------------------------------------------

# Interactive launcher. It lists every target that carries a `## description`,
```

Замініть на:

```make
# targets keep working exactly as before (CI and pre-commit call them directly),
# and a bare `make` opens the same menu.

# Bare `make` shows the command menu.
.DEFAULT_GOAL := do_good

.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check keycloak-up keycloak-down keycloak-reset check hooks-install hooks docs-check

# ---------------------------------------------------------------------------
# Command menu
# ---------------------------------------------------------------------------

# Interactive launcher. It lists every target that carries a `## description`,
```

**2.** Знайдіть:

```make
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

ml-check: ## Type-check and test the local BGE-M3 and reranker servers (Python 3.12)
	cd ml/bge_m3_server && uv run pyright && uv run pytest -q

# ---------------------------------------------------------------------------
# Combined checks and git hooks
# ---------------------------------------------------------------------------

check: ## Run the full local gate (format, lint, types, tests + coverage)
	uv run ruff format --check .
	uv run ruff check .
```

Замініть на:

```make
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

ml-check: ## Type-check and test the local BGE-M3 and reranker servers (Python 3.12)
	cd ml/bge_m3_server && uv run pyright && uv run pytest -q

# ---------------------------------------------------------------------------
# Local identity provider
# ---------------------------------------------------------------------------

# Keycloak for local work only (M07-T02). Its secrets live in
# deploy/keycloak/.env, created on the first `keycloak-up` and ignored by Git.
# The realm is imported only into an empty volume, so new secrets beside an
# old volume would not match the stored passwords: `keycloak-up` refuses that.
KEYCLOAK_COMPOSE = docker compose -f deploy/keycloak/compose.yaml
KEYCLOAK_PROJECT = knowledge-service-keycloak
KEYCLOAK_VOLUME = $(KEYCLOAK_PROJECT)_keycloak-data

keycloak-up: ## Start the local Keycloak, creating its secrets on first use
	@if [ ! -f deploy/keycloak/.env ] && docker volume inspect $(KEYCLOAK_VOLUME) >/dev/null 2>&1; then \
		echo "deploy/keycloak/.env is gone but Keycloak's volume still holds a realm made with it; run make keycloak-reset"; \
		exit 1; \
	fi
	uv run python scripts/keycloak_env.py
	$(KEYCLOAK_COMPOSE) up -d --wait

keycloak-down: ## Stop the local Keycloak and keep its users and keys
	docker compose -p $(KEYCLOAK_PROJECT) down

# By project name, not by file, so it works even when .env is missing.
keycloak-reset: ## Remove the local Keycloak, its volume, and its secrets
	docker compose -p $(KEYCLOAK_PROJECT) down -v
	rm -f deploy/keycloak/.env

# ---------------------------------------------------------------------------
# Combined checks and git hooks
# ---------------------------------------------------------------------------

check: ## Run the full local gate (format, lint, types, tests + coverage)
	uv run ruff format --check .
	uv run ruff check .
```

Перевірка: `git check-ignore -v deploy/keycloak/.env` називає правило `.env` у `.gitignore`, тож цей файл ніколи не потрапить у коміт.

### Крок 3 — Крок із кодом: закріплений файл Compose

Цей крок каже, який саме Keycloak працює і як.

**Простими словами:** Файл Compose запускає один контейнер Keycloak з образу, зафіксованого digest, доступний лише з вашої машини на порту 8180. Він передає секрети з файлу, який зробив крок 2, і не стартує без будь-якого з них. Файл realm монтується лише для читання, щоб його імпортувати, дані Keycloak лежать в іменованому томі, а здоровим контейнер вважається лише тоді, коли Keycloak готовий.

| Налаштування | Чому |
|---|---|
| `image: ...:26.8.1@sha256:...` | Версія — для людей, digest — для машин; перезалитий тег не змінить того, що працює |
| `start-dev --import-realm` | Режим розробки з імпортом файлу realm під час першого запуску |
| `KC_HOSTNAME: http://localhost:8180` | Кожен токен називає того самого issuer, хоч за якою адресою звертався клієнт |
| `${NAME:?run make keycloak-up}` | Compose зупиняється раніше, ніж Keycloak міг би імпортувати відсутній секрет |
| `127.0.0.1:8180:8080` | Недосяжний з інших машин; порт керування 9000 не публікується |
| `keycloak-data:/opt/keycloak/data` | Користувачі, сесії і згенеровані ключі; цією папкою володіє образ, тож новий том отримує його власника, а не root |
| `healthcheck` | Питає `/health/ready` на порту 9000 всередині контейнера, тож `up --wait` повертається, лише коли вхід працює |

Створіть `deploy/keycloak/compose.yaml`:

```yaml
# Local, non-production Keycloak for the knowledge service (M07-T02).
#
# Start it with `make keycloak-up`, which first creates `.env` beside this
# file with random secrets. Nothing here holds a credential: every secret is
# read from that file, and Compose refuses to start when one is missing.
name: knowledge-service-keycloak

services:
  keycloak:
    # Keycloak 26.8.1, pinned by digest so every machine runs the same image.
    image: quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332
    command: ["start-dev", "--import-realm"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_BOOTSTRAP_ADMIN_PASSWORD:?run make keycloak-up}
      # Tokens name this issuer whichever address the client used.
      KC_HOSTNAME: http://localhost:8180
      KC_HEALTH_ENABLED: "true"
      # Read by the realm file's placeholders when the realm is imported.
      KS_OPEN_WEBUI_CLIENT_SECRET: ${KS_OPEN_WEBUI_CLIENT_SECRET:?run make keycloak-up}
      KS_ALICE_PASSWORD: ${KS_ALICE_PASSWORD:?run make keycloak-up}
      KS_BOB_PASSWORD: ${KS_BOB_PASSWORD:?run make keycloak-up}
      KS_CAROL_PASSWORD: ${KS_CAROL_PASSWORD:?run make keycloak-up}
    ports:
      - "127.0.0.1:8180:8080"
    volumes:
      # The realm is imported once, on the first start with an empty volume.
      - ./knowledge-service-realm.json:/opt/keycloak/data/import/knowledge-service-realm.json:ro
      # Users, sessions, and the generated signing keys live here. The image
      # owns this directory, so a new volume gets its owner, not root's.
      - keycloak-data:/opt/keycloak/data
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          exec 3<>/dev/tcp/127.0.0.1/9000 &&
          printf 'GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3 &&
          grep -q '"status": "UP"' <&3
      interval: 5s
      timeout: 5s
      retries: 60

volumes:
  keycloak-data:
```

Перевірка: `docker compose -f deploy/keycloak/compose.yaml config` до запуску скрипта з кроку 2 падає з `required variable ... is missing a value: run make keycloak-up`, називаючи один із секретів.

### Крок 4 — Крок із кодом: realm

Цей крок описує realm, який імпортує Keycloak: хто існує, хто звідки може увійти і що кажуть їхні токени.

**Простими словами:** Файл realm створює realm `knowledge-service` з двома групами, чотирма ролями застосунку і трьома демо-користувачами, чиї паролі беруться з файлу секретів. Він реєструє Open WebUI як єдиний застосунок, що може впускати користувачів, лише через вхід у браузері з PKCE і лише назад на одну точну адресу, і вимикає вбудований вхід `admin-cli`. Він каже Keycloak класти в токени групи користувача й аудиторію API, не додавати нічого іншого, що попросить клієнт, і генерувати власні ключі підпису.

| Частина | Що містить |
|---|---|
| Налаштування realm | Без самореєстрації і скидання пароля, захист від перебору, `RS256`, access tokens на 5 хвилин |
| `roles`, `groups`, `users` | Чотири ролі застосунку, `engineering` і `finance`, а також alice, bob, carol з фіксованими `id` і паролями `${...}` |
| `open-webui` | Конфіденційний, лише code flow, `pkce.code.challenge.method: S256`, один redirect URI і web origin, фіксовані scopes за замовчуванням, жодних необовʼязкових, `fullScopeAllowed: false` |
| Protocol mappers | `groups` повним шляхом у кожному токені; `knowledge-service-api` в аудиторії access token |
| `admin-cli` | Вбудований клієнт Keycloak, з вимкненими всіма flows і grants |
| `scopeMappings` | У токені `open-webui` можуть зʼявитися лише чотири ролі застосунку |
| `components` | Постачальники ключів: `rsa-generated` (RS256), `hmac-generated`, `aes-generated`; жодного ключового матеріалу |

Створіть `deploy/keycloak/knowledge-service-realm.json`:

```json
{
  "realm": "knowledge-service",
  "displayName": "Knowledge service (local, non-production)",
  "enabled": true,
  "sslRequired": "external",
  "registrationAllowed": false,
  "resetPasswordAllowed": false,
  "rememberMe": false,
  "loginWithEmailAllowed": false,
  "duplicateEmailsAllowed": false,
  "bruteForceProtected": true,
  "defaultSignatureAlgorithm": "RS256",
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "ssoSessionMaxLifespan": 36000,
  "roles": {
    "realm": [
      {
        "name": "user",
        "description": "Asks questions and manages their own Conversations."
      },
      {
        "name": "content-manager",
        "description": "Configures Sources and starts Synchronization Runs."
      },
      {
        "name": "evaluator",
        "description": "Manages Evaluation Datasets and runs paid evaluation."
      },
      {
        "name": "administrator",
        "description": "Manages policy and operational configuration."
      }
    ]
  },
  "groups": [
    {
      "name": "engineering"
    },
    {
      "name": "finance"
    }
  ],
  "users": [
    {
      "id": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01",
      "username": "alice",
      "enabled": true,
      "email": "alice@example.test",
      "emailVerified": true,
      "firstName": "Alice",
      "lastName": "Demo",
      "credentials": [
        {
          "type": "password",
          "value": "${KS_ALICE_PASSWORD}",
          "temporary": false
        }
      ],
      "groups": [
        "/engineering"
      ],
      "realmRoles": [
        "user"
      ]
    },
    {
      "id": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a02",
      "username": "bob",
      "enabled": true,
      "email": "bob@example.test",
      "emailVerified": true,
      "firstName": "Bob",
      "lastName": "Demo",
      "credentials": [
        {
          "type": "password",
          "value": "${KS_BOB_PASSWORD}",
          "temporary": false
        }
      ],
      "groups": [
        "/finance"
      ],
      "realmRoles": [
        "user",
        "content-manager"
      ]
    },
    {
      "id": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03",
      "username": "carol",
      "enabled": true,
      "email": "carol@example.test",
      "emailVerified": true,
      "firstName": "Carol",
      "lastName": "Demo",
      "credentials": [
        {
          "type": "password",
          "value": "${KS_CAROL_PASSWORD}",
          "temporary": false
        }
      ],
      "groups": [],
      "realmRoles": [
        "user",
        "administrator"
      ]
    }
  ],
  "clients": [
    {
      "clientId": "open-webui",
      "name": "Open WebUI (local)",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": false,
      "clientAuthenticatorType": "client-secret",
      "secret": "${KS_OPEN_WEBUI_CLIENT_SECRET}",
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,
      "fullScopeAllowed": false,
      "frontchannelLogout": true,
      "redirectUris": [
        "http://localhost:3000/oauth/oidc/callback"
      ],
      "webOrigins": [
        "http://localhost:3000"
      ],
      "attributes": {
        "pkce.code.challenge.method": "S256",
        "post.logout.redirect.uris": "http://localhost:3000/"
      },
      "protocolMappers": [
        {
          "name": "groups",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-group-membership-mapper",
          "config": {
            "claim.name": "groups",
            "full.path": "true",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true"
          }
        },
        {
          "name": "knowledge-service-api audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "config": {
            "included.custom.audience": "knowledge-service-api",
            "id.token.claim": "false",
            "access.token.claim": "true"
          }
        }
      ],
      "defaultClientScopes": [
        "basic",
        "acr",
        "web-origins",
        "profile",
        "email",
        "roles"
      ],
      "optionalClientScopes": []
    },
    {
      "clientId": "admin-cli",
      "enabled": true,
      "publicClient": true,
      "standardFlowEnabled": false,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false
    }
  ],
  "scopeMappings": [
    {
      "client": "open-webui",
      "roles": [
        "user",
        "content-manager",
        "evaluator",
        "administrator"
      ]
    }
  ],
  "components": {
    "org.keycloak.keys.KeyProvider": [
      {
        "name": "rsa-generated",
        "providerId": "rsa-generated",
        "config": {
          "priority": [
            "100"
          ],
          "keySize": [
            "2048"
          ],
          "algorithm": [
            "RS256"
          ]
        }
      },
      {
        "name": "hmac-generated-hs512",
        "providerId": "hmac-generated",
        "config": {
          "priority": [
            "100"
          ],
          "algorithm": [
            "HS512"
          ]
        }
      },
      {
        "name": "aes-generated",
        "providerId": "aes-generated",
        "config": {
          "priority": [
            "100"
          ]
        }
      }
    ]
  }
}
```

Перевірка: `python3 -m json.tool deploy/keycloak/knowledge-service-realm.json > /dev/null` проходить успішно.

### Крок 5 — Без коду: запустіть Keycloak і подивіться на нього

Цей крок запускає Keycloak і вручну перевіряє, що він публікує.

**Дія 1:** запустіть його. Docker має працювати.

```bash
make keycloak-up
```

Очікувано: `created .env with 5 secrets`, потім контейнер зі статусом `Healthy`. Перший запуск триває до хвилини.

**Дія 2:** прочитайте, що публікує realm:

```bash
curl -s http://localhost:8180/realms/knowledge-service/.well-known/openid-configuration | python3 -m json.tool | grep -E '"(issuer|jwks_uri)"'
curl -s http://localhost:8180/realms/knowledge-service/protocol/openid-connect/certs | python3 -m json.tool | grep -E '"(kid|alg|use)"'
docker port knowledge-service-keycloak-keycloak-1
```

Очікувано: issuer `http://localhost:8180/realms/knowledge-service`, один ключ з `"alg": "RS256"` і `"use": "sig"`, і лише `8080/tcp -> 127.0.0.1:8180`.

Перевірка: `ls -l deploy/keycloak/.env` показує `-rw-------`, а `git status` його не показує.

### Крок 6 — Крок із кодом: тести

Цей крок доводить конфігурацію у файлах і в запущеному Keycloak.

**Простими словами:** Помічник впускає користувача так, як це робить браузер: відкриває сторінку входу, надсилає імʼя і пароль, ловить повернений код і обмінює його на токени з PKCE verifier і секретом клієнта. Якщо Keycloak відмовляє на будь-якому кроці, помічник каже, на якому, і передає власну причину Keycloak. Unit-тести читають realm, файл Compose і скрипт секретів. Інтеграційні тести впускають демо-користувачів і пробують усе, що має провалитися.

| Тест | Що доводить |
|---|---|
| `test_a_demo_user_logs_in_and_gets_a_signed_token_for_the_api`, `test_each_demo_user_carries_their_groups_and_roles_only` | Успіх: токен RS256 з ключем із JWKS, точні issuer і аудиторія, фіксований `sub`, групи і ролі кожного користувача |
| `test_a_code_is_never_sent_to_an_unregistered_redirect` | Найризикованіший край: сім майже правильних redirect URI, кожен відхилено з `Invalid parameter: redirect_uri` |
| `test_a_client_cannot_add_scopes_that_change_the_token` | Другий край: жоден scope не може покласти ролі в `groups` чи додати offline-токен |
| `test_a_wrong_password_gets_the_login_form_again_and_no_code`, `test_a_login_without_an_s256_challenge_is_refused`, `test_a_code_is_not_exchanged_without_the_client_secret`, `test_no_client_gives_a_token_for_a_password_alone` | Типізовані відмови, кожна з причиною від Keycloak |
| `tests/test_keycloak_config.py` | Жодних облікових даних у репозиторії, кожен заповнювач вимагає Compose і створює скрипт, закріплення, лише loopback, права файлу скрипта і його відмови |

Чому помічник надсилає кукі сам: Keycloak позначає свої кукі входу як `Secure`. Браузер усе одно надсилає їх на `http://localhost`, який вважає безпечним; httpx — ні.

Створіть `tests/keycloak_flow.py`:

```python
"""Log in to the local Keycloak as a browser would, for integration tests.

The flow is the one Open WebUI uses: authorization code with PKCE. The
helper asks for the login page, posts the user's name and password to the
form Keycloak returns, takes the code from the redirect without following
it, and exchanges the code for tokens. Each step that Keycloak refuses
raises `LoginRefused` with the step, the status, and Keycloak's own reason,
never a secret.
"""

import base64
import hashlib
import html
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ISSUER = "http://localhost:8180/realms/knowledge-service"
CLIENT_ID = "open-webui"
REDIRECT_URI = "http://localhost:3000/oauth/oidc/callback"
SECRETS = Path(__file__).resolve().parents[1] / "deploy/keycloak/.env"
FORM_ACTION = re.compile(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"')
# The message on Keycloak's error page, and the one above its login form.
PAGE_ERROR = re.compile(r'id="kc-error-message">\s*<p class="instruction">([^<]*)<')
FORM_ERROR = re.compile(r'kc-feedback-text">\s*([^<]*?)\s*<')


class LoginRefused(Exception):
    """Keycloak refused one step of the login.

    `step` names the step, `status` is Keycloak's HTTP status, and `reason`
    is the message Keycloak gave: the text of its error page or login form,
    or the `error` and `error_description` of a redirect.
    """

    def __init__(self, step: str, status: int, reason: str) -> None:
        super().__init__(f"{step} refused with status {status}: {reason}")
        self.step = step
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Tokens:
    access_token: str
    id_token: str


def local_secrets(path: Path = SECRETS) -> dict[str, str]:
    """The generated secrets, read from the ignored `.env` file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines if "=" in line]
    return {name: value for name, value in pairs if not name.startswith("#")}


def claims(token: str) -> dict[str, Any]:
    """The payload of a JWT, decoded but not verified; M07-T03 verifies."""
    return _segment(token.split(".")[1])


def header(token: str) -> dict[str, Any]:
    """The header of a JWT, decoded but not verified."""
    return _segment(token.split(".")[0])


async def log_in(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    *,
    client_secret: str,
    redirect_uri: str = REDIRECT_URI,
    scope: str = "openid",
    challenge: str | None = "S256",
) -> Tokens:
    """Log `username` in through the login form and return their tokens.

    `challenge` is the PKCE method to ask for; `None` sends no challenge.
    """
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if challenge == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        query["code_challenge"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    elif challenge is not None:
        query["code_challenge"] = verifier
    if challenge is not None:
        query["code_challenge_method"] = challenge
    page = await client.get(f"{ISSUER}/protocol/openid-connect/auth", params=query)
    action = FORM_ACTION.search(page.text) if page.status_code == 200 else None
    if action is None:
        raise LoginRefused("authorization request", page.status_code, _reason(page))

    answer = await client.post(
        html.unescape(action[1]),
        data={"username": username, "password": password, "credentialId": ""},
        headers={"Cookie": _session_cookies(page)},
    )
    location: str = answer.headers.get("location", "")
    returned: dict[str, list[str]] = parse_qs(urlsplit(location).query)
    if answer.status_code != 302 or not location.startswith(redirect_uri):
        raise LoginRefused("login form", answer.status_code, _reason(answer))
    if returned.get("state") != [state] or "code" not in returned:
        raise LoginRefused("redirect", answer.status_code, _reason(answer))

    exchanged = await client.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": returned["code"][0],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        auth=(CLIENT_ID, client_secret),
    )
    if exchanged.status_code != 200:
        raise LoginRefused("code exchange", exchanged.status_code, _reason(exchanged))
    body = exchanged.json()
    return Tokens(access_token=body["access_token"], id_token=body["id_token"])


def _reason(response: httpx.Response) -> str:
    """What Keycloak said, from a redirect, a JSON error, or a page."""
    location: str = response.headers.get("location", "")
    if location:
        parts = urlsplit(location)
        said: dict[str, list[str]] = parse_qs(parts.query or parts.fragment)
        return " ".join(said.get("error", []) + said.get("error_description", []))
    if response.headers.get("content-type", "").startswith("application/json"):
        body: dict[str, str] = response.json()
        return f"{body.get('error', '')} {body.get('error_description', '')}".strip()
    found = PAGE_ERROR.search(response.text) or FORM_ERROR.search(response.text)
    return html.unescape(found[1]).strip() if found else ""


def _session_cookies(response: httpx.Response) -> str:
    """The login session's cookies, as a `Cookie` header.

    Keycloak marks them `Secure`. A browser still sends them to
    `http://localhost`, which it treats as secure; httpx does not, so the
    helper passes them on itself.
    """
    cookies = response.headers.get_list("set-cookie")
    return "; ".join(cookie.split(";", 1)[0] for cookie in cookies)


def _segment(part: str) -> dict[str, Any]:
    decoded = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    return json.loads(decoded)  # type: ignore[no-any-return]
```

Створіть `tests/test_keycloak_config.py`:

```python
"""The local Keycloak configuration: pinned, exact, and free of credentials."""

import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYCLOAK = ROOT / "deploy/keycloak"
SCRIPT = ROOT / "scripts/keycloak_env.py"
PLACEHOLDER = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
REQUIRED = re.compile(r"^\s+([A-Z0-9_]+): \$\{\1:\?[^}]+\}$", re.MULTILINE)


def realm() -> dict[str, Any]:
    return json.loads((KEYCLOAK / "knowledge-service-realm.json").read_text())


def realm_client(client_id: str = "open-webui") -> dict[str, Any]:
    (found,) = [c for c in realm()["clients"] if c["clientId"] == client_id]
    return found


def compose() -> str:
    return (KEYCLOAK / "compose.yaml").read_text()


def generate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def secrets_in(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    pairs = [line.split("=", 1) for line in lines if not line.startswith("#")]
    return dict(pairs)


def strings(value: object) -> list[str]:
    """Every string anywhere in a JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings(item)]  # type: ignore[misc]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]  # type: ignore[misc]
    return []


# --- The realm ------------------------------------------------------------------------


def test_the_realm_has_the_client_groups_roles_and_demo_users() -> None:
    config = realm()

    assert config["realm"] == "knowledge-service"
    assert [client["clientId"] for client in config["clients"]] == [
        "open-webui",
        "admin-cli",
    ]
    assert [group["name"] for group in config["groups"]] == ["engineering", "finance"]
    assert {role["name"] for role in config["roles"]["realm"]} == {
        "user",
        "content-manager",
        "evaluator",
        "administrator",
    }
    assert {
        user["username"]: (user["groups"], user["realmRoles"])
        for user in config["users"]
    } == {
        "alice": (["/engineering"], ["user"]),
        "bob": (["/finance"], ["user", "content-manager"]),
        "carol": ([], ["user", "administrator"]),
    }


def test_each_demo_user_keeps_one_id_across_resets() -> None:
    ids = [UUID(user["id"]) for user in realm()["users"]]

    assert len(set(ids)) == len(ids) == 3


def test_the_client_uses_only_the_code_flow_with_pkce_and_exact_redirects() -> None:
    client = realm_client()

    assert client["publicClient"] is False
    assert client["standardFlowEnabled"] is True
    assert client["implicitFlowEnabled"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["redirectUris"] == ["http://localhost:3000/oauth/oidc/callback"]
    assert client["webOrigins"] == ["http://localhost:3000"]
    assert not any("*" in uri for uri in client["redirectUris"] + client["webOrigins"])


def test_a_client_cannot_ask_for_more_scopes_or_roles_than_these() -> None:
    client = realm_client()

    assert client["defaultClientScopes"] == [
        "basic",
        "acr",
        "web-origins",
        "profile",
        "email",
        "roles",
    ]
    assert client["optionalClientScopes"] == []
    assert client["fullScopeAllowed"] is False
    assert realm()["scopeMappings"] == [
        {
            "client": "open-webui",
            "roles": ["user", "content-manager", "evaluator", "administrator"],
        }
    ]


def test_the_built_in_admin_cli_gives_no_tokens_in_this_realm() -> None:
    admin = realm_client("admin-cli")

    assert admin["directAccessGrantsEnabled"] is False
    assert admin["standardFlowEnabled"] is False
    assert admin["implicitFlowEnabled"] is False
    assert admin["serviceAccountsEnabled"] is False


def test_tokens_carry_groups_by_full_path_and_the_api_audience() -> None:
    client = realm_client()
    mappers = {
        mapper["protocolMapper"]: mapper["config"]
        for mapper in client["protocolMappers"]
    }

    groups = mappers["oidc-group-membership-mapper"]
    assert (groups["claim.name"], groups["full.path"]) == ("groups", "true")
    assert groups["access.token.claim"] == "true"
    audience = mappers["oidc-audience-mapper"]
    assert audience["included.custom.audience"] == "knowledge-service-api"
    assert audience["access.token.claim"] == "true"


def test_signing_keys_are_generated_by_keycloak_never_committed() -> None:
    config = realm()
    providers = config["components"]["org.keycloak.keys.KeyProvider"]

    assert config["defaultSignatureAlgorithm"] == "RS256"
    assert {provider["providerId"] for provider in providers} == {
        "rsa-generated",
        "hmac-generated",
        "aes-generated",
    }
    held = {key for provider in providers for key in provider["config"]}
    assert not held & {"privateKey", "certificate", "secret", "kid"}


def test_every_credential_in_the_realm_is_a_placeholder() -> None:
    config = realm()
    values = [realm_client()["secret"]] + [
        credential["value"]
        for user in config["users"]
        for credential in user["credentials"]
    ]

    assert all(PLACEHOLDER.fullmatch(value) for value in values)
    assert all(
        credential["temporary"] is False
        for user in config["users"]
        for credential in user["credentials"]
    )


def test_every_placeholder_is_required_by_compose_and_made_by_the_script(
    tmp_path: Path,
) -> None:
    placeholders = {
        match[1]
        for value in strings(realm())
        if (match := PLACEHOLDER.fullmatch(value)) is not None
    }
    generate(tmp_path / ".env")

    required = set(REQUIRED.findall(compose()))
    assert placeholders <= required
    assert required == set(secrets_in(tmp_path / ".env"))


def test_no_placeholder_hides_inside_a_longer_value() -> None:
    partial = [
        value
        for value in strings(realm())
        if "${" in value and PLACEHOLDER.fullmatch(value) is None
    ]

    assert partial == []


# --- Compose --------------------------------------------------------------------------


def test_the_image_is_pinned_by_version_and_digest() -> None:
    (image,) = re.findall(r"^\s+image: (\S+)$", compose(), re.MULTILINE)

    assert re.fullmatch(
        r"quay\.io/keycloak/keycloak:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", image
    )


def test_keycloak_listens_on_the_loopback_address_only() -> None:
    text = compose()
    (block,) = re.findall(r"^    ports:\n((?:      .*\n)+)", text, re.MULTILINE)

    assert block.splitlines() == ['      - "127.0.0.1:8180:8080"']
    assert "network_mode" not in text
    assert "expose:" not in text


def test_the_realm_is_mounted_read_only_and_imported_on_start() -> None:
    text = compose()

    assert (
        "./knowledge-service-realm.json:"
        "/opt/keycloak/data/import/knowledge-service-realm.json:ro"
    ) in text
    assert 'command: ["start-dev", "--import-realm"]' in text


# --- The secrets script ---------------------------------------------------------------


def test_the_script_creates_random_owner_only_secrets_and_prints_none(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"

    done = generate(path)

    assert done.returncode == 0
    values = secrets_in(path)
    assert len(set(values.values())) == len(values) == 5
    assert all(len(value) >= 32 for value in values.values())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not any(value in done.stdout + done.stderr for value in values.values())
    generate(tmp_path / "other.env")
    other = secrets_in(tmp_path / "other.env")
    assert not set(values.values()) & set(other.values())


def test_an_existing_file_is_kept_because_the_realm_already_uses_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    generate(path)
    before = path.read_text()

    done = generate(path)

    assert done.returncode == 0
    assert "kept" in done.stdout
    assert path.read_text() == before


@pytest.mark.parametrize(
    "content",
    ["", "KS_ALICE_PASSWORD=x\n", "# KS_BOB_PASSWORD=x\n"],
    ids=["empty", "partial", "commented"],
)
def test_a_file_without_every_secret_is_refused_without_changing_it(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / ".env"
    path.write_text(content)

    done = generate(path)

    assert done.returncode == 1
    assert "keycloak-reset" in done.stderr
    assert path.read_text() == content


def test_the_generated_file_is_ignored_by_git() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "deploy/keycloak/.env"],  # noqa: S607
        cwd=ROOT,
        check=False,
    )

    assert ignored.returncode == 0
```

Створіть `tests/test_keycloak_integration.py`:

```python
"""Login and token issuance against the local Keycloak (`make keycloak-up`).

The tests skip themselves when nothing listens on Keycloak's port or its
generated secrets are missing. A Keycloak that answers without the realm
fails them instead.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from keycloak_flow import (
    CLIENT_ID,
    ISSUER,
    REDIRECT_URI,
    SECRETS,
    LoginRefused,
    claims,
    header,
    local_secrets,
    log_in,
)

pytestmark = pytest.mark.integration

PASSWORDS = {
    "alice": "KS_ALICE_PASSWORD",
    "bob": "KS_BOB_PASSWORD",
    "carol": "KS_CAROL_PASSWORD",
}
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            found = await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        assert found.status_code == 200, "Keycloak answers, but without the realm"
        yield client


@pytest.fixture
def secrets() -> dict[str, str]:
    return local_secrets()


async def log_in_as(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    username: str,
    **options: Any,
) -> dict[str, Any]:
    """The claims of `username`'s access token, logged in with `options`."""
    tokens = await log_in(
        client,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
        **options,
    )
    return claims(tokens.access_token)


async def refused(
    client: httpx.AsyncClient, secrets: dict[str, str], **options: Any
) -> LoginRefused:
    """Why alice's login with `options` was refused."""
    with pytest.raises(LoginRefused) as raised:
        await log_in_as(client, secrets, "alice", **options)
    return raised.value


# --- Success --------------------------------------------------------------------------


async def test_discovery_names_the_issuer_and_its_keys(
    client: httpx.AsyncClient,
) -> None:
    found = (await client.get(f"{ISSUER}/.well-known/openid-configuration")).json()

    assert found["issuer"] == ISSUER
    assert found["jwks_uri"] == f"{ISSUER}/protocol/openid-connect/certs"


async def test_a_demo_user_logs_in_and_gets_a_signed_token_for_the_api(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    tokens = await log_in(
        client,
        "alice",
        secrets["KS_ALICE_PASSWORD"],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )

    keys = (await client.get(f"{ISSUER}/protocol/openid-connect/certs")).json()
    signing = {key["kid"]: key for key in keys["keys"] if key["use"] == "sig"}
    access = header(tokens.access_token)
    assert access["alg"] == "RS256"
    assert signing[access["kid"]]["kty"] == "RSA"
    payload = claims(tokens.access_token)
    assert payload["iss"] == ISSUER
    assert payload["aud"] == "knowledge-service-api"
    assert payload["azp"] == CLIENT_ID
    assert payload["sub"] == "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
    assert payload["exp"] - payload["iat"] == 300
    assert claims(tokens.id_token)["aud"] == CLIENT_ID


@pytest.mark.parametrize(
    ("username", "groups", "roles"),
    [
        ("alice", ["/engineering"], {"user"}),
        ("bob", ["/finance"], {"user", "content-manager"}),
        ("carol", [], {"user", "administrator"}),
    ],
)
async def test_each_demo_user_carries_their_groups_and_roles_only(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    username: str,
    groups: list[str],
    roles: set[str],
) -> None:
    payload = await log_in_as(client, secrets, username)

    assert payload.get("groups", []) == groups
    assert set(payload["realm_access"]["roles"]) == roles


# --- The highest-risk edges: where a code goes, and what a token may claim ------------


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:3000/oauth/oidc/callback/extra",
        "http://localhost:3000/oauth/oidc/callback?next=http://evil.test",
        "http://localhost:3000.evil.test/oauth/oidc/callback",
        "http://localhost:3000@evil.test/oauth/oidc/callback",
        "http://localhost:3001/oauth/oidc/callback",
        "https://localhost:3000/oauth/oidc/callback",
        "http://evil.test/oauth/oidc/callback",
    ],
    ids=[
        "longer-path",
        "query",
        "suffix-host",
        "userinfo",
        "other-port",
        "other-scheme",
        "other-host",
    ],
)
async def test_a_code_is_never_sent_to_an_unregistered_redirect(
    client: httpx.AsyncClient, secrets: dict[str, str], redirect_uri: str
) -> None:
    refusal = await refused(client, secrets, redirect_uri=redirect_uri)

    assert (refusal.step, refusal.status) == ("authorization request", 400)
    assert refusal.reason == "Invalid parameter: redirect_uri"


@pytest.mark.parametrize(
    "scope",
    ["openid microprofile-jwt", "openid offline_access", "openid address phone"],
    ids=["roles-as-groups", "offline", "other-optional"],
)
async def test_a_client_cannot_add_scopes_that_change_the_token(
    client: httpx.AsyncClient, secrets: dict[str, str], scope: str
) -> None:
    refusal = await refused(client, secrets, scope=scope)

    assert refusal.step == "authorization request"
    assert refusal.reason.startswith("invalid_scope")


# --- Denials --------------------------------------------------------------------------


async def test_a_wrong_password_gets_the_login_form_again_and_no_code(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    refusal = await refused(
        client,
        {**secrets, "KS_ALICE_PASSWORD": secrets["KS_BOB_PASSWORD"]},
    )

    assert (refusal.step, refusal.status) == ("login form", 200)
    assert refusal.reason == "Invalid username or password."


@pytest.mark.parametrize(
    ("challenge", "reason"),
    [
        (None, "invalid_request Missing parameter: code_challenge_method"),
        (
            "plain",
            "invalid_request Invalid parameter: "
            "code challenge method is not matching the configured one",
        ),
    ],
    ids=["none", "plain"],
)
async def test_a_login_without_an_s256_challenge_is_refused(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    challenge: str | None,
    reason: str,
) -> None:
    refusal = await refused(client, secrets, challenge=challenge)

    assert (refusal.step, refusal.reason) == ("authorization request", reason)


async def test_a_code_is_not_exchanged_without_the_client_secret(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    wrong = {**secrets, "KS_OPEN_WEBUI_CLIENT_SECRET": secrets["KS_BOB_PASSWORD"]}

    refusal = await refused(client, wrong)

    assert (refusal.step, refusal.status) == ("code exchange", 401)
    assert refusal.reason.startswith("unauthorized_client")


@pytest.mark.parametrize(
    "client_id",
    [
        "open-webui",
        "admin-cli",
        "account",
        "account-console",
        "security-admin-console",
        "broker",
        "realm-management",
    ],
)
async def test_no_client_gives_a_token_for_a_password_alone(
    client: httpx.AsyncClient, secrets: dict[str, str], client_id: str
) -> None:
    data = {
        "grant_type": "password",
        "client_id": client_id,
        "username": "carol",
        "password": secrets["KS_CAROL_PASSWORD"],
    }
    # Only open-webui has a secret; the built-in clients are tried without one.
    secret = secrets["KS_OPEN_WEBUI_CLIENT_SECRET"]
    answer = await (
        client.post(TOKEN_ENDPOINT, data=data, auth=(client_id, secret))
        if client_id == CLIENT_ID
        else client.post(TOKEN_ENDPOINT, data=data)
    )

    assert answer.status_code in {400, 401}
    assert answer.json()["error"] == "unauthorized_client"
    assert "access_token" not in answer.json()


async def test_the_client_has_no_token_of_its_own(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    answer = await client.post(
        TOKEN_ENDPOINT,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, secrets["KS_OPEN_WEBUI_CLIENT_SECRET"]),
    )

    assert answer.json()["error"] == "unauthorized_client"
    assert "access_token" not in answer.json()


async def test_the_implicit_flow_returns_no_token(client: httpx.AsyncClient) -> None:
    answer = await client.get(
        f"{ISSUER}/protocol/openid-connect/auth",
        params={
            "response_type": "token",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
            "nonce": "n",
        },
    )

    location = answer.headers.get("location", "")
    assert "error=unauthorized_client" in location
    assert "access_token" not in location
```

Запустіть:

```bash
uv run pytest tests/test_keycloak_config.py -q
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Очікувано: 19 passed, потім 28 passed. Із зупиненим Keycloak друга команда дає 28 skipped.

### Крок 7 — Без коду: зламайте, скиньте і вирішіть

Цей крок показує, що захищає налаштування scopes, чому зміна realm потребує скидання і що чистий том відтворює все.

**Дія 1:** навмисно зламайте клієнта. У `deploy/keycloak/knowledge-service-realm.json` замініть `"optionalClientScopes": []` на `"optionalClientScopes": ["microprofile-jwt"]`. Запустіть обидва тестові файли:

```bash
uv run pytest tests/test_keycloak_config.py -q
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Очікувано: unit-тести дають 1 failed, 18 passed (`test_a_client_cannot_ask_for_more_scopes_or_roles_than_these`). Інтеграційні тести досі дають 28 passed: Keycloak лишив realm, який уже імпортував, а змінений файл навіть не читався.

**Дія 2:** скиньте і запустіть знову, потім повторіть інтеграційні тести:

```bash
make keycloak-reset
make keycloak-up
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Очікувано: 1 failed, 27 passed: `test_a_client_cannot_add_scopes_that_change_the_token[roles-as-groups]`. З цим scope токен carol каже `"groups": ["administrator", "user"]`, і код, що довіряв би `groups` для доступу до Документів, сприйняв би роль за групу.

**Дія 3:** відкотіть зміну, потім ще раз скиньте, запустіть і перевірте. Це й перевірка приймання: чистий том із новими секретами і новими ключами підпису, а ті самі входи працюють.

```bash
make keycloak-reset
make keycloak-up
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Очікувано: 28 passed.

**Дія 4:** під `## Decision` напишіть:

- чому redirect URI має збігатися точно і що дав би код, надісланий деінде;
- чому клієнт не може просити необовʼязкові scopes і чому `groups` має містити лише групи;
- чому паролі живуть в ігнорованому файлі, а realm містить заповнювачі;
- чому ключі підпису генеруються і ніколи не комітяться і що це означає для M07-T03;
- хоча б одне **спростоване припущення** у формі «Я припускав X. Запуск чи ревʼю показує Y. Отже, Z хибне».

### Крок 8 — Без коду: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат.

**Дія 1:** оновіть два документи.

У `README.md`:

Знайдіть:

````markdown
make lint              Run Ruff linting
make typecheck         Run Pyright
make test-unit         Run deterministic unit tests
make test-integration  Run non-live integration tests
make check             Run every non-live quality gate with coverage
make docs-check        Validate the documentation under docs/
```

Example of a direct command: UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-unit

The menu is built from the `## description` comment on each target, so a new
command shows up in it automatically once you document it:

````

Замініть на:

````markdown
make lint              Run Ruff linting
make typecheck         Run Pyright
make test-unit         Run deterministic unit tests
make test-integration  Run non-live integration tests
make check             Run every non-live quality gate with coverage
make docs-check        Validate the documentation under docs/
make keycloak-up       Start the local Keycloak (creates its secrets once)
make keycloak-down     Stop the local Keycloak, keeping its users and keys
make keycloak-reset    Remove the local Keycloak, its volume, and its secrets
```

The local Keycloak (`deploy/keycloak/`) serves the `knowledge-service` realm at
`http://localhost:8180` for development only. `make keycloak-up` writes random
secrets to the Git-ignored `deploy/keycloak/.env` on first use; the demo users'
passwords are read from there. The realm is imported only into an empty volume,
so if that file is lost or edited, run `make keycloak-reset` and then
`make keycloak-up`. Keycloak briefly locks a user after quick failed logins;
wait a minute or reset.

Example of a direct command: UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-unit

The menu is built from the `## description` comment on each target, so a new
command shows up in it automatically once you document it:

````

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, later OIDC client secrets | Environment and settings, redacted in repr |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
```

Замініть на:

```markdown
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
```

**2.** Знайдіть:

```markdown

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

Замініть на:

```markdown

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

**3.** Знайдіть:

```markdown

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

Замініть на:

```markdown

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Дія 2:** запустіть перевірки по черзі, коли Keycloak працює:

```bash
uv run pytest tests/test_keycloak_config.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2381 passed для `make test-unit`, на 19 більше, ніж після M07-T01, і 406 passed для `make test-integration`, на 28 більше. Без Keycloak ці 28 пропускаються.

**Дія 3:** заповніть файл evidence:

- **Design:** файл Compose, realm, де живуть секрети, і помічник входу.
- **Verification:** команди й числа, вивід кроку 5, навмисна поломка до і після скидання, запуск із чистого тому і відомі обмеження з «Перш ніж почати».
- **Review:** підсумок ревʼю з «Перш ніж почати».
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу завершеною, додайте рядок M07-T02 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T03 і зробіть коміт `m07-t02: run keycloak locally`.

## Запитання для рефлексії

1. Keycloak ховає від API багато чого: паролі, сторінки входу, сесії, ротацію ключів. Що лишається, що API все одно має перевірити в кожному токені, і чому воно не може довіряти, що Keycloak це вже зробив?
2. Access token каже `"groups": ["/engineering"]`. Яка наступна задача перетворить це на Group ID, які використовують Access Grants, і що має статися з назвою групи, якої сервіс ніколи не бачив?
3. Яке налаштування realm найлегше послабити «лише для тестування» пізніше, і який тест це впіймає?
4. Скидання дає нові ключі підпису. Що зламалося б, якби M07-T03 скопіював сьогоднішній публічний ключ у файл конфігурації?
5. Carol — адміністратор без групи. Що вона має побачити, коли питає про Документ engineering, і чому?

## Чек-лист завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, написані до змін.
- [ ] `make keycloak-up` один раз створює секрети, запускає закріплений Keycloak і показує його здоровим.
- [ ] Realm має один клієнт застосунку з code flow, PKCE S256, одним точним redirect URI і без необовʼязкових scopes; `admin-cli` не видає токенів.
- [ ] Ключі підпису генерує Keycloak, а репозиторій не містить жодного пароля, секрету чи ключа.
- [ ] Демо-користувачі входять через форму і отримують токени RS256 з аудиторією API, своїми групами повним шляхом і лише своїми ролями застосунку.
- [ ] Незареєстровані redirect URI, зайві scopes, неправильні паролі, відсутній PKCE, неправильний секрет клієнта і вхід лише за паролем — усе відхиляється.
- [ ] Ви виконали навмисну поломку, побачили, що до скидання вона нічого не змінює, побачили падіння тесту після нього і відкотили її.
- [ ] Скидання і новий запуск відтворюють вхід і видачу токенів.
- [ ] `README.md` і `docs/threat-model.md` описують локальний Keycloak.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт зроблено.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чек-листом.
