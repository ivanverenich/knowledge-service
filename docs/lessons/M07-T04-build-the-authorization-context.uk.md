# M07-T04 — Побудова Authorization Context

Англійська версія: [M07-T04-build-the-authorization-context.md](M07-T04-build-the-authorization-context.md)

Задача: [M07-T04 — Build the Authorization Context](../curriculum/milestones/M07-identity-and-authorization/M07-T04-build-the-authorization-context.md)

## Що ви зробите

У цьому уроці перевірка токенів із M07-T03 стає перед answer-ендпоінтами, а перевірений токен перетворюється на ідентичність, від імені якої йде пошук. Досі джерело Evidence шукало від імені того, для кого його побудували, і ніщо в запиті не казало, хто питає. Наприкінці кожен запит на відповідь потребує дійсного bearer-токена, і читач (reader) може взятися тільки з полів цього токена.

- **Одні двері:** обидва answer-ендпоінти читають один заголовок `Authorization: Bearer`, перевіряють його і будують Authorization Context ще до читання тіла запиту.
- **Типізована ідентичність:** ID користувача і шляхи груп із токена стають `UserId` і `GroupId`. Вони однакові для кожного токена і прив'язані до видавця.
- **Більше нічого не рахується:** жоден інший заголовок, cookie чи поле тіла не може назвати чи розширити читача.
- **Відмови:** відсутній чи поганий токен дає 401, зіпсований заголовок — 400, а недосяжний провайдер ідентичності — 503. Завеликі, зіпсовані чи недовірені поля відхиляються, і текст токена не потрапляє ні у відповідь, ні в лог.

| Задача вимагає | Де це відбувається |
|---|---|
| Перетворити перевірені subject і групи на типізовану внутрішню ідентичність | Крок 3: `src/knowledge_service/identity/context.py` |
| Відхиляти завеликі, зіпсовані чи недовірені поля від клієнта | Крок 3, і крок 2 для назв груп, які Keycloak зробив би неоднозначними |
| Тести: успіх, найризикованіший край, типізована відмова | Крок 6: `tests/test_authorization_context.py` |
| Тести ендпоінтів доводять, що Authorization Context створює лише вихід перевіряльника | Кроки 4 і 6: `src/knowledge_service/app.py`, `tests/test_app_authentication.py` |
| Оновити лише те, що зміна зробила неточним | Крок 8: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–6 пишуть код і тести. Кроки 1, 7 і 8 коду не потребують, хоча крок 8 змінює два документи.

Терміни цього уроку:

- **Authorization Context:** перевірений User і його Groups для одного запиту. Пошук повертає лише ті Chunks, які грант (Access Grant) дає всім, цьому User або одній із цих Groups.
- **Bearer-токен:** токен доступу, який клієнт надсилає як `Authorization: Bearer <token>`. Хто його пред'явив, того й вважають власником, тому токен треба перевіряти.
- **Claim (поле токена):** одне поле токена: `sub` (користувач), `groups` (шляхи його груп, як-от `/engineering`), `azp` (клієнт, якому видано токен), `iss` (видавець).
- **UUIDv5:** UUID, обчислений з назви. Та сама назва завжди дає той самий UUID, як відбиток пальця. Таблиця, щоб його пам'ятати, не потрібна.
- **401, 400, 503:** 401 означає «немає дійсної ідентичності, увійдіть»; 400 — «ваш запит зіпсований»; 503 — «зараз ми не можемо перевіряти ідентичність, спробуйте пізніше».
- **RFC 6750:** стандарт bearer-токенів у HTTP. Він каже, який статус і який заголовок `WWW-Authenticate` отримує кожна відмова.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код. Тоді кожен крок буде зрозумілим, коли ви до нього дійдете.

- **ID з назв, а не з таблиці.** User — це видавець плюс `sub` токена; Group — видавець плюс один повний шлях групи. Кожне стає UUIDv5 під фіксованим простором імен. Тож alice отримує той самий `UserId` при кожному вході, а та сама назва від іншого видавця ніколи не збігається. Шлях групи, якого не називає жоден грант, просто нічому не відповідає. Групи з джерел (Confluence тощо) отримають власне зіставлення в M07-T06.
- **Шляхи — точно як написано.** `/Engineering` і `/engineering` — дві групи, як і в Keycloak. Шлях зберігає регістр, і нічого не нормалізується.
- **Екрановані слеші і жодної `~`.** За замовчуванням Keycloak дає групі верхнього рівня `a/b` той самий шлях, що й групі `b` всередині групи `a`, і обидві стали б однією Group. Крок 2 вмикає в Keycloak екранування: `/` усередині назви записується як `~/`. Саму `~` Keycloak не екранує, тому шлях із `~` все ще може означати дві групи. Такий шлях відкидається.
- **Одна дивна група коштує тільки себе.** Якщо поле `groups` не є списком рядків або містить понад 64 назви, відхиляється весь токен. Окремий неправильний шлях (без `/` на початку, з `~`, з невидимим символом, довший за 255 символів) відкидається і рахується в лозі. Так користувач втрачає лише цю групу, а не всі інші.
- **Лише довірені клієнти.** `azp` токена має бути клієнтом, якому сервіс довіряє, тут `open-webui`. Сьогодні аудиторію API має тільки цей клієнт; перевірка зберігає це, навіть якщо додадуть інший.
- **Тіло чекає на ідентичність.** FastAPI читає і розбирає тіло запиту ще до запуску залежностей. Тому ендпоінти приймають лише `Request`: спершу автентифікація, і лише потім читання тіла, щонайбільше 64 КіБ, і розбір як `Question`. Анонімний запит не змусить сервіс буферизувати велике тіло.
- **Читач іде до самого низу.** `AnswerStreamer.open` і `EvidenceSource.find` тепер приймають `reader`. Шлях M01 без опори на документи, `POST /v1/answer`, теж потребує токена, але нічого не шукає, тож читача не передає.
- **Без автентифікатора нікому не відповідають.** `create_app` приймає `authenticator`; без нього обидва answer-ендпоінти відповідають 503. Наявні тести отримують замінник, який приймає один фіксований токен.
- **Що знайшов рев'ю.** Незалежний атакувальний рев'ю пробував підроблені заголовки, параметри запиту, cookie і поля тіла, справжні токени інших клієнтів, згенеровані поля, сирі ASGI-запити і справжній Keycloak. Способу обрати чи розширити читача він не знайшов. Знайдені проблеми виправлено, і для кожної є тест:
  - Keycloak дав групі верхнього рівня `zzr18/child` і групі `child` всередині `zzr18` однаковий шлях, тож обидві ставали однією Group. Тепер Keycloak екранує слеші, а шляхи з `~` відкидаються.
  - FastAPI читав і розбирав тіло до автентифікації: анонімний запит міг надіслати 125 МіБ, а зламаний JSON отримував 422 замість 401. Тепер ідентичність перевіряється першою, а тіло обмежено 64 КіБ.
  - Назви із символами, що друкуються як ніщо, наприклад заповнювач хангиля, проходили як видимі. Тепер рахуються лише літери, діакритичні знаки, цифри, пунктуація, символи і пробіли всередині, мінус список порожніх символів.
  - Одна погана група повністю блокувала користувача. Тепер відкидається лише ця група.
  - Відмови не відповідали RFC 6750: заголовок `Basic` отримував код помилки, а зіпсований заголовок — 401. Тепер інша схема отримує простий виклик (challenge), а зіпсований заголовок — 400.
  - Неочікувана помилка всередині автентифікатора ставала 500. Тепер це 503, і в лог іде лише тип помилки.
  - Два заголовки `Authorization`, назви яких відрізнялися регістром, бачилися не обидва. Тепер кожен заголовок порівнюється в нижньому регістрі.
  - Дві зміни коду переживали всі тести: streamer перестає перевіряти читача, і шляхи груп зводяться до нижнього регістру. Тепер на кожну є тест.
- **Відомі обмеження.** Точки входу, що збирає застосунок для продакшну, ще немає; хто її напише, має передати `Authenticator`. Жодне джерело Evidence над базою ще не приймає читача (M07-T07, M07-T08). Схема API більше не показує тіло запиту, бо FastAPI його більше не розбирає.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої прогнози

Перш ніж щось змінювати, запишіть, що має бути правдою наприкінці уроку і чого ви очікуєте. Крок 7 перевірить прогнози.

Створіть `docs/evidence/M07-T04-build-the-authorization-context.md`:

```markdown
# M07-T04 — Build the Authorization Context evidence

## Invariant

A request to `POST /v1/answer` or `POST /v1/answer/stream` gets an
Authorization Context only from one `Authorization: Bearer` header whose
token `OidcVerifier` accepts, issued to a trusted client: its User is the
issuer and `sub`, its Groups the issuer and each plain full group path, as
UUIDv5 IDs. No other header, cookie, or body field can name or widen it, and
the body is not read before it exists. Malformed headers get 400, missing or
bad tokens 401, an unreachable identity provider or a missing authenticator
503. No token or claim text reaches a response or a log.

## Expected failing check

Before the change, `uv run pytest tests/test_app_authentication.py -q` fails
with `ModuleNotFoundError: No module named 'knowledge_service.identity.context'`.

## Predictions

- What a request with no Authorization header and a body of `{not json` gets, before and after the change:
- What carol's Authorization Context holds, and what a question about an engineering Document returns for her:
- What happens to a user whose token lists `/engineering` and `/fin~ance`:
- Which test fails if paths with `~` are kept:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі є ваша відповідь.

### Крок 2 — Крок із кодом: Keycloak екранує слеші в назвах груп

**Простими словами:** цей крок змінює одне налаштування Keycloak, щоб дві різні групи ніколи не мали спільного шляху. Група верхнього рівня `a/b` записуватиметься як `/a~/b`, а група `b` всередині `a` лишиться `/a/b`. Тест закріплює налаштування, щоб його ніхто випадково не прибрав.

| Файл | Зміна |
|---|---|
| `deploy/keycloak/compose.yaml` | `KC_SPI_GROUP__JPA__ESCAPE_SLASHES_IN_GROUP_PATH: "true"` |
| `tests/test_keycloak_config.py` | Тест, що налаштування на місці |

У `deploy/keycloak/compose.yaml`:

Знайдіть:

```yaml
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
```

Замініть на:

```yaml
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_BOOTSTRAP_ADMIN_PASSWORD:?run make keycloak-up}
      # Tokens name this issuer whichever address the client used.
      KC_HOSTNAME: http://localhost:8180
      KC_HEALTH_ENABLED: "true"
      # Keycloak writes "/" inside a group name as "~/", so a top-level group
      # named "a/b" never has the path of group "b" inside group "a".
      KC_SPI_GROUP__JPA__ESCAPE_SLASHES_IN_GROUP_PATH: "true"
      # Read by the realm file's placeholders when the realm is imported.
      KS_OPEN_WEBUI_CLIENT_SECRET: ${KS_OPEN_WEBUI_CLIENT_SECRET:?run make keycloak-up}
      KS_ALICE_PASSWORD: ${KS_ALICE_PASSWORD:?run make keycloak-up}
      KS_BOB_PASSWORD: ${KS_BOB_PASSWORD:?run make keycloak-up}
      KS_CAROL_PASSWORD: ${KS_CAROL_PASSWORD:?run make keycloak-up}
    ports:
```

У `tests/test_keycloak_config.py`:

Знайдіть:

```python

    assert block.splitlines() == ['      - "127.0.0.1:8180:8080"']
    assert "network_mode" not in text
    assert "expose:" not in text


def test_the_realm_is_mounted_read_only_and_imported_on_start() -> None:
    text = compose()

    assert (
        "./knowledge-service-realm.json:"
        "/opt/keycloak/data/import/knowledge-service-realm.json:ro"
```

Замініть на:

```python

    assert block.splitlines() == ['      - "127.0.0.1:8180:8080"']
    assert "network_mode" not in text
    assert "expose:" not in text


def test_a_slash_inside_a_group_name_is_escaped_in_its_path() -> None:
    # Without it, a top-level group named "a/b" has the path of group "b"
    # inside group "a", and both become one Group in the Authorization Context.
    assert re.search(
        r'^\s+KC_SPI_GROUP__JPA__ESCAPE_SLASHES_IN_GROUP_PATH: "true"$',
        compose(),
        re.MULTILINE,
    )


def test_the_realm_is_mounted_read_only_and_imported_on_start() -> None:
    text = compose()

    assert (
        "./knowledge-service-realm.json:"
        "/opt/keycloak/data/import/knowledge-service-realm.json:ro"
```

Потім перезапустіть Keycloak. Налаштування діє з запуску, тож скидання не потрібне, і том зберігає дані:

```bash
make keycloak-up
```

**Перевірка:** `uv run pytest tests/test_keycloak_config.py -q` дає `20 passed`, а `make keycloak-up` показує контейнер `Healthy`.

### Крок 3 — Крок із кодом: від перевірених полів до Authorization Context

**Простими словами:** цей файл — двері, через які проходить кожен запит на відповідь. Він бере заголовок `Authorization`, перевіряє, що це один Bearer-токен, дає перевіряльнику з M07-T03 перевірити токен і перетворює імена користувача і груп на типізовані ID, зрозумілі пошуку. Усе зіпсоване, завелике або від клієнта, якому сервіс не довіряє, відхиляється з названою причиною. Результат — єдиний спосіб, у який запит отримує читача.

| Частина | Що робить |
|---|---|
| `IDENTITY_NAMESPACE`, `user_id_for`, `group_id_for` | ID UUIDv5 з видавця і назви; фіксовані назавжди |
| `AuthenticationError`, `Unauthenticated` | Відмова з причиною для логів і помилкою RFC 6750 для відповіді |
| `ClaimDefect` | Чому поля не можуть стати контекстом: видавець, subject, недовірений клієнт, зіпсовані чи завеликі групи |
| `TokenVerifier` | Що автентифікатору потрібно від перевіряльника; `OidcVerifier` підходить |
| `ContextPolicy` | Довірені клієнти, щонайбільше 64 групи, назви до 255 символів |
| `build_authorization_context` | Перевіряє `iss`, `azp` і `sub`, потім залишає звичайні шляхи груп |
| `Authenticator.authenticate` | Один Bearer-заголовок, потім перевіряльник, потім зіставлення |
| `_plain_path`, `_visible_name` | Повний шлях без `~`, і кожен символ видимий |

Створіть `src/knowledge_service/identity/context.py`:

```python
"""Turn a request's bearer token into the Authorization Context.

`Authenticator.authenticate` is the only way a request gets an
`AuthorizationContext`: it reads one `Authorization: Bearer` header, has the
token verified, and maps the verified claims to typed identifiers. Nothing
else in a request, no other header and no field of the body, names the User
or their Groups.

The mapping is deterministic and needs no storage. A User is the issuer and
the token's `sub`; a Group is the issuer and one full group path, such as
`/engineering`. Each becomes a UUIDv5, so the same person or group always
gets the same ID, and the same name from another issuer never collides with
it. A group path no Access Grant names simply matches nothing.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.oidc import TokenRejected, VerifiedToken

logger = logging.getLogger(__name__)

# Fixed forever: changing it would give every User and Group a new ID.
IDENTITY_NAMESPACE = UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31")
# RFC 6750 §2.1: the scheme, one or more spaces, and a b64token.
BEARER = re.compile(r"Bearer +([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE | re.ASCII)
# Letters, marks that combine with them, digits, punctuation, and symbols.
VISIBLE = frozenset(
    {"Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Nd", "Nl", "No"}
    | {"Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sm", "Sc", "Sk", "So"}
)
# Characters in those categories that still print as nothing: the combining
# grapheme joiner, Hangul and Khmer fillers, Mongolian and other variation
# selectors, and the blank Braille pattern.
BLANK = frozenset(
    chr(code)
    for code in (
        0x034F,
        0x115F,
        0x1160,
        0x17B4,
        0x17B5,
        0x180B,
        0x180C,
        0x180D,
        0x180F,
        0x2800,
        0x3164,
        0xFFA0,
        *range(0xFE00, 0xFE10),
    )
)


class AuthenticationError(StrEnum):
    """The RFC 6750 error a refused request gets.

    `NONE` is a request with no Bearer token at all: it gets a bare
    challenge. `INVALID_REQUEST` is a malformed Authorization header, a 400.
    `INVALID_TOKEN` is a token that is not good, a 401.
    """

    NONE = ""
    INVALID_REQUEST = "invalid_request"
    INVALID_TOKEN = "invalid_token"  # noqa: S105 - an RFC 6750 error code


class Unauthenticated(Exception):
    """The request carries no usable identity; `reason` names why, for logs."""

    def __init__(self, reason: str, *, error: AuthenticationError) -> None:
        super().__init__(f"unauthenticated: {reason}")
        self.reason = reason
        self.error = error


class ClaimDefect(StrEnum):
    """Why verified claims cannot become an Authorization Context."""

    ISSUER = "issuer"
    SUBJECT = "subject"
    UNTRUSTED_CLIENT = "untrusted_client"
    GROUPS_MALFORMED = "groups_malformed"
    GROUPS_OVERSIZED = "groups_oversized"


class TokenVerifier(Protocol):
    """Verifies a bearer token; `OidcVerifier` is the implementation."""

    async def verify(self, token: str) -> VerifiedToken: ...


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Which clients may call, and how large the identity claims may be."""

    trusted_clients: frozenset[str]
    max_groups: int = 64
    max_name_length: int = 255

    def __post_init__(self) -> None:
        if type(self.trusted_clients) is not frozenset or not self.trusted_clients:
            raise ValueError("trusted_clients must be a non-empty frozenset")
        if self.max_groups <= 0 or self.max_name_length <= 0:
            raise ValueError("limits must be positive")


def user_id_for(issuer: str, subject: str) -> UserId:
    """The User an issuer's subject is, the same for every token."""
    return UserId(uuid5(IDENTITY_NAMESPACE, f"user\x00{issuer}\x00{subject}"))


def group_id_for(issuer: str, path: str) -> GroupId:
    """The Group an issuer's full group path is, the same for every token.

    The path is used exactly as the provider wrote it: `/Engineering` and
    `/engineering` are two groups, as they are in Keycloak.
    """
    return GroupId(uuid5(IDENTITY_NAMESPACE, f"group\x00{issuer}\x00{path}"))


def build_authorization_context(
    token: VerifiedToken, policy: ContextPolicy
) -> AuthorizationContext:
    """The User and Groups a verified token names, or `Unauthenticated`.

    The token must come from a trusted client (`azp`), name a visible
    subject, and carry a `groups` claim, if any, that is a list of at most
    `max_groups` strings. A path that is not a plain full path is left
    out, so one odd group costs its member that group, never the others.
    """
    claims = token.claims
    issuer: object = claims.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise _defect(ClaimDefect.ISSUER)
    client: object = claims.get("azp")
    if not isinstance(client, str) or client not in policy.trusted_clients:
        raise _defect(ClaimDefect.UNTRUSTED_CLIENT)
    if not _visible_name(token.subject, policy):
        raise _defect(ClaimDefect.SUBJECT)
    return AuthorizationContext(
        user_id=user_id_for(issuer, token.subject),
        group_ids=frozenset(
            group_id_for(issuer, path) for path in _group_paths(claims, policy)
        ),
    )


class Authenticator:
    """Turns one request's `Authorization` header into its context."""

    def __init__(self, verifier: TokenVerifier, policy: ContextPolicy) -> None:
        self._verifier = verifier
        self._policy = policy

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        """The context for the request's `Authorization` header values.

        Raises `Unauthenticated` for no Bearer header, more than one header,
        a malformed one, a token the verifier rejects, or claims that cannot
        be mapped; the verifier's `IdentityProviderUnavailable` passes
        through.
        """
        if len(headers) > 1:
            raise Unauthenticated("repeated", error=AuthenticationError.INVALID_REQUEST)
        if not headers or not headers[0].lower().startswith("bearer"):
            # No token at all, or another scheme such as Basic.
            raise Unauthenticated("missing", error=AuthenticationError.NONE)
        found = BEARER.fullmatch(headers[0])
        if found is None:
            raise Unauthenticated(
                "malformed", error=AuthenticationError.INVALID_REQUEST
            )
        try:
            verified = await self._verifier.verify(found[1])
        except TokenRejected as rejected:
            reason = str(rejected.reason)
        else:
            return build_authorization_context(verified, self._policy)
        # Raised outside `except`, so nothing about the token is chained.
        raise Unauthenticated(reason, error=AuthenticationError.INVALID_TOKEN)


def _group_paths(claims: Mapping[str, Any], policy: ContextPolicy) -> set[str]:
    """The distinct plain full group paths in the `groups` claim."""
    listed: object = claims.get("groups", [])
    if not isinstance(listed, list):
        raise _defect(ClaimDefect.GROUPS_MALFORMED)
    paths = cast("list[object]", listed)
    if len(paths) > policy.max_groups:
        raise _defect(ClaimDefect.GROUPS_OVERSIZED)
    named: set[str] = set()
    for path in paths:
        if not isinstance(path, str):
            raise _defect(ClaimDefect.GROUPS_MALFORMED)
        named.add(path)
    kept = {path for path in named if _plain_path(path, policy)}
    if ignored := len(named - kept):
        # A count only: the names may be anything the provider holds.
        logger.warning("group claim entries ignored: %d", ignored)
    return kept


def _plain_path(path: str, policy: ContextPolicy) -> bool:
    """A full path such as `/finance/payroll`, with nothing ambiguous in it.

    Keycloak writes a `/` inside a group's name as `~/`, but leaves `~`
    itself alone, so a path with `~` could be either of two groups.
    """
    return (
        path.startswith("/")
        and path != "/"
        and "~" not in path
        and _visible_name(path, policy)
    )


def _visible_name(name: str, policy: ContextPolicy) -> bool:
    """Non-empty, within the length limit, and every character visible.

    Spaces are allowed inside a name, not at either end.
    """
    return (
        0 < len(name) <= policy.max_name_length
        and name == name.strip()
        and all(
            char == " " or (unicodedata.category(char) in VISIBLE and char not in BLANK)
            for char in name
        )
    )


def _defect(defect: ClaimDefect) -> Unauthenticated:
    return Unauthenticated(str(defect), error=AuthenticationError.INVALID_TOKEN)
```

Дві деталі легко пропустити:

- `BLANK` побудовано через `chr()` з кодів символів, а не набрано самими символами. Символ, що друкується як ніщо, невидимий у редакторі, і його легко загубити або випадково додати.
- `authenticate` створює `Unauthenticated` після блоку `except`, як і перевіряльник, тож до відмови не прикріплюється жодна помилка з текстом токена.

**Перевірка:** `uv run pyright src/knowledge_service/identity` показує `0 errors`.

### Крок 4 — Крок із кодом: ендпоінти спершу автентифікують, і читач доходить до пошуку

**Простими словами:** цей крок ставить двері перед обома answer-ендпоінтами. Кожен запит автентифікується ще до того, як прочитано його тіло; лише потім тіло читається, до 64 КіБ, і перевіряється як Question. Потокова відповідь передає читача джерелу Evidence, тож пошук іде від імені цього User і його Groups. Відмови отримують статус і заголовок виклику, яких вимагає RFC 6750.

| Де | Зміна |
|---|---|
| `EvidenceSource.find`, `AnswerStreamer.open` | Приймають `reader: AuthorizationContext`; `open` відхиляє все інше |
| `create_app` | Приймає `authenticator`; без нього обидва answer-ендпоінти відповідають 503 |
| `admit`, `authenticate` | Спершу читач, з кожного заголовка `Authorization` у будь-якому регістрі, потім Question |
| Обробники винятків | 400 або 401 з `WWW-Authenticate`; 503 для недосяжного провайдера чи неочікуваної помилки; 413 для тіла понад ліміт |
| `read_question` | Читає щонайбільше `MAX_BODY_BYTES` і розбирає `Question`, або дає звичайну помилку валідації |

У `src/knowledge_service/answering/streaming.py`:

**1.** Знайдіть:

```python
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

import anyio

from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
```

Замініть на:

```python
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

import anyio

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
```

**2.** Знайдіть:

```python
    signals: EvidenceSignals


class EvidenceSource(Protocol):
    """Finds the Evidence the reader may see for one Question."""

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence: ...


@dataclass(frozen=True, slots=True)
class FinishedAnswer:
    """The checked result and the policy's decision about it."""

```

Замініть на:

```python
    signals: EvidenceSignals


class EvidenceSource(Protocol):
    """Finds the Evidence the reader may see for one Question."""

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence: ...


@dataclass(frozen=True, slots=True)
class FinishedAnswer:
    """The checked result and the policy's decision about it."""

```

**3.** Знайдіть:

```python
        self._model = model
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(self, question: Question, *, request_id: UUID) -> AnswerStream:
        """Find and judge the Evidence, then return the stream of events.

        Raises before any byte is sent: `AnswerDeadlineExceeded` when finding
        Evidence takes the whole deadline, the source's own errors, and the
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id)
        try:
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds
            async with _before(deadline):
                found = await self._source.find(question.text, request_id=request_id)
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
            sufficiency = assess_evidence(found.signals, self._policy)
            context = (
                allocate_context(question.text, found.evidence, self._budget)
                if sufficiency is Sufficiency.SUFFICIENT
```

Замініть на:

```python
        self._model = model
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(
        self, question: Question, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerStream:
        """Find and judge the Evidence `reader` may see, then return the stream.

        Raises before any byte is sent: `AnswerDeadlineExceeded` when finding
        Evidence takes the whole deadline, the source's own errors, and the
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id)
        try:
            if type(reader) is not AuthorizationContext:
                raise InvalidAnswerRequest("reader must be an AuthorizationContext")
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds
            async with _before(deadline):
                found = await self._source.find(
                    question.text, reader=reader, request_id=request_id
                )
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
            sufficiency = assess_evidence(found.signals, self._policy)
            context = (
                allocate_context(question.text, found.evidence, self._budget)
                if sufficiency is Sufficiency.SUFFICIENT
```

У `src/knowledge_service/app.py`:

**1.** Знайдіть:

```python
import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.workflow import AnswerWorkflow

ReadinessCheck = Callable[[], Awaitable[bool]]


def error_response(
    *,
    status_code: int,
    code: str,
```

Замініть на:

```python
import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identity.context import AuthenticationError, Unauthenticated
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.workflow import AnswerWorkflow

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]
# A Question is at most 4,000 characters; even escaped as JSON it fits.
MAX_BODY_BYTES = 65_536


class RequestAuthenticator(Protocol):
    """Gives a request's Authorization Context from its `Authorization` headers.

    `Authenticator` is the implementation; it raises `Unauthenticated`, or
    `IdentityProviderUnavailable` when tokens cannot be checked.
    """

    async def authenticate(self, headers: list[str]) -> AuthorizationContext: ...


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error


class _CannotAuthenticate(Exception):
    pass


class _BodyTooLarge(Exception):
    pass


def error_response(
    *,
    status_code: int,
    code: str,
```

**2.** Знайдіть:

```python


def create_app(
    readiness_check: ReadinessCheck | None = None,
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
) -> FastAPI:
    app = FastAPI(title="knowledge-service")

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
```

Замініть на:

```python


def create_app(
    readiness_check: ReadinessCheck | None = None,
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
) -> FastAPI:
    """The HTTP application.

    Both answer endpoints need a bearer token: without an `authenticator`
    they answer 503, and the reader is only ever the context it returns.
    """
    app = FastAPI(title="knowledge-service")

    async def admit(request: Request) -> tuple[AuthorizationContext, Question]:
        """The reader, then the Question: the body is not read without a reader."""
        reader = await authenticate(request)
        return reader, await read_question(request)

    async def authenticate(request: Request) -> AuthorizationContext:
        if authenticator is None:
            raise _CannotAuthenticate
        # Every Authorization header, whatever the case of its name.
        headers = [
            value.decode("latin-1")
            for name, value in request.headers.raw
            if name.lower() == b"authorization"
        ]
        try:
            return await authenticator.authenticate(headers)
        except Unauthenticated as refused:
            logger.info("request not authenticated: %s", refused.reason)
            error = refused.error
        except IdentityProviderUnavailable:
            logger.warning("request not authenticated: identity provider unavailable")
            raise _CannotAuthenticate from None
        except Exception as unexpected:
            # Only the type: the message may quote the token or its claims.
            logger.error("authentication failed: %s", type(unexpected).__name__)
            raise _CannotAuthenticate from None
        raise _NotAuthenticated(error)

    @app.exception_handler(_NotAuthenticated)
    async def not_authenticated_handler(
        request: Request, refused: _NotAuthenticated
    ) -> JSONResponse:
        malformed = refused.error is AuthenticationError.INVALID_REQUEST
        response = error_response(
            status_code=400 if malformed else 401,
            code="invalid_request" if malformed else "unauthenticated",
            message=(
                "The Authorization header is malformed"
                if malformed
                else "A valid access token is required"
            ),
            request_id=request_id_from(request),
        )
        # RFC 6750 §3: no error code when no Bearer token was sent.
        response.headers["WWW-Authenticate"] = (
            f'Bearer error="{refused.error}"' if refused.error else "Bearer"
        )
        return response

    @app.exception_handler(_CannotAuthenticate)
    async def cannot_authenticate_handler(
        request: Request, _error: _CannotAuthenticate
    ) -> JSONResponse:
        return failure_response(
            kind=FailureKind.DEPENDENCY, request_id=request_id_from(request)
        )

    @app.exception_handler(_BodyTooLarge)
    async def body_too_large_handler(
        request: Request, _error: _BodyTooLarge
    ) -> JSONResponse:
        return error_response(
            status_code=413,
            code="request_too_large",
            message="The request body is too large",
            request_id=request_id_from(request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
```

**3.** Знайдіть:

```python
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready"},
        )

    @app.post("/v1/answer", response_model=Answer)
    async def answer(
        question: Question,
        request: Request,
    ) -> Answer | JSONResponse:
        request_id = request_id_from(request)

        if workflow is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
```

Замініть на:

```python
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready"},
        )

    @app.post("/v1/answer", response_model=Answer)
    async def answer(request: Request) -> Answer | JSONResponse:
        # The ungrounded M01 path searches nothing, so it needs proof of
        # identity but no reader; M09-T01 replaces it.
        _reader, question = await admit(request)
        request_id = request_id_from(request)

        if workflow is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
```

**4.** Знайдіть:

```python
            return failure_response(
                kind=classify_failure(error),
                request_id=request_id,
            )

    @app.post("/v1/answer/stream")
    async def answer_stream(question: Question, request: Request) -> Response:
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request, streamer.open(question, request_id=request_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing was sent yet, so a failure is still a plain HTTP error.
            return failure_response(kind=classify_failure(error), request_id=request_id)
```

Замініть на:

```python
            return failure_response(
                kind=classify_failure(error),
                request_id=request_id,
            )

    @app.post("/v1/answer/stream")
    async def answer_stream(request: Request) -> Response:
        reader, question = await admit(request)
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request, streamer.open(question, reader=reader, request_id=request_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing was sent yet, so a failure is still a plain HTTP error.
            return failure_response(kind=classify_failure(error), request_id=request_id)
```

**5.** Знайдіть:

```python
                "X-Accel-Buffering": "no",
                "X-Request-ID": str(request_id),
            },
        )

    return app
```

Замініть на:

```python
                "X-Accel-Buffering": "no",
                "X-Request-ID": str(request_id),
            },
        )

    return app


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _BodyTooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise _BodyTooLarge
    try:
        return Question.model_validate_json(bytes(body))
    except ValidationError as invalid:
        raise RequestValidationError(invalid.errors()) from None
```

**Перевірка:** `uv run pyright src` показує `0 errors`. Наявні тести ендпоінтів тепер падають, їх 64: у їхніх застосунках немає автентифікатора, тому ендпоінти відповідають 503, а їхні виклики `open` не передають `reader`. Крок 5 це виправляє.

### Крок 5 — Крок із кодом: дайте наявним тестам ідентичність

**Простими словами:** тести стримінгу, збоїв і застосунку не про ідентичність, але їхні запити тепер її потребують. Цей крок додає автентифікатор-замінник, який приймає один фіксований токен і дає одного фіксованого читача, і передає його кожному застосунку, який будують ці тести. Джерела Evidence в цих тестах теж отримують новий аргумент `reader` і записують його.

Створіть `tests/identity_fakes.py`:

```python
"""A stand-in authenticator for endpoint tests that are not about identity.

It accepts one fixed bearer token and gives one fixed reader, so tests of
streaming and failures can call the protected endpoints. The identity tests
use the real `Authenticator`.
"""

from uuid import UUID

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import AuthenticationError, Unauthenticated

READER = AuthorizationContext(
    user_id=UserId(UUID(int=0xA11CE)), group_ids=frozenset({GroupId(UUID(int=0xE))})
)
AUTHORIZATION = {"Authorization": "Bearer reader-token"}


class StaticAuthenticator:
    """Gives `READER` for `AUTHORIZATION`, and refuses anything else."""

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        if headers == [AUTHORIZATION["Authorization"]]:
            return READER
        error = (
            AuthenticationError.INVALID_TOKEN if headers else AuthenticationError.NONE
        )
        raise Unauthenticated("test", error=error)


TRUSTED = StaticAuthenticator()
```

У `tests/test_streaming.py`:

**1.** Знайдіть:

```python
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.types import Message

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
```

Замініть на:

```python
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.types import Message

from evidence_fakes import MARKER, candidate
from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
```

**2.** Знайдіть:

```python
        wait: bool = False,
    ) -> None:
        self.found = FoundEvidence(EVIDENCE, SIGNALS) if found is None else found
        self.error = error
        self.wait = wait
        self.cancelled = False

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        if self.wait:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
```

Замініть на:

```python
        wait: bool = False,
    ) -> None:
        self.found = FoundEvidence(EVIDENCE, SIGNALS) if found is None else found
        self.error = error
        self.wait = wait
        self.cancelled = False
        self.readers: list[AuthorizationContext] = []

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        self.readers.append(reader)
        if self.wait:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
```

**3.** Знайдіть:

```python
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
) -> list[bytes]:
    events = await answering.open(question, request_id=REQUEST_ID)
    return [chunk async for chunk in events]


def rebuilt(sent: list[bytes]) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in sent:
```

Замініть на:

```python
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
) -> list[bytes]:
    events = await answering.open(question, reader=READER, request_id=REQUEST_ID)
    return [chunk async for chunk in events]


def rebuilt(sent: list[bytes]) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in sent:
```

**4.** Знайдіть:

```python
    assert names(sent)[:2] == ["metadata", "text_delta"]
    assert names(sent)[-3:] == ["citation", "usage", "completion"]


async def test_text_reaches_the_client_before_the_model_has_finished() -> None:
    model = Model(hold=4)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)

    first = [await anext(events) for _ in range(4)]
    assert names(first) == ["metadata", "text_delta", "text_delta", "text_delta"]
    assert not model.finished
    model.release.set()
    rest = [chunk async for chunk in events]
```

Замініть на:

```python
    assert names(sent)[:2] == ["metadata", "text_delta"]
    assert names(sent)[-3:] == ["citation", "usage", "completion"]


async def test_text_reaches_the_client_before_the_model_has_finished() -> None:
    model = Model(hold=4)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)

    first = [await anext(events) for _ in range(4)]
    assert names(first) == ["metadata", "text_delta", "text_delta", "text_delta"]
    assert not model.finished
    model.release.set()
    rest = [chunk async for chunk in events]
```

**5.** Знайдіть:

```python


async def test_a_source_failure_is_raised_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(error=AuthorizationDenied()))

    with pytest.raises(AuthorizationDenied):
        await answering.open(QUESTION, request_id=REQUEST_ID)


async def test_a_slow_source_runs_out_of_the_deadline_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(wait=True), timeout_seconds=0.1)

    with pytest.raises(AnswerDeadlineExceeded):
        await answering.open(QUESTION, request_id=REQUEST_ID)


# --- Cancellation ---------------------------------------------------------------


async def test_closing_the_stream_closes_the_model_and_records_a_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await events.aclose()

    assert model.closed
```

Замініть на:

```python


async def test_a_source_failure_is_raised_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(error=AuthorizationDenied()))

    with pytest.raises(AuthorizationDenied):
        await answering.open(QUESTION, reader=READER, request_id=REQUEST_ID)


async def test_a_slow_source_runs_out_of_the_deadline_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(wait=True), timeout_seconds=0.1)

    with pytest.raises(AnswerDeadlineExceeded):
        await answering.open(QUESTION, reader=READER, request_id=REQUEST_ID)


# --- Cancellation ---------------------------------------------------------------


async def test_closing_the_stream_closes_the_model_and_records_a_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await events.aclose()

    assert model.closed
```

**6.** Знайдіть:

```python

async def test_a_stream_closed_before_its_first_event_ends_its_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)

    await events.aclose()

    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_model_whose_close_never_ends_cannot_hold_the_stream() -> None:
    model = Model(hold=2, close_delay=60)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await asyncio.wait_for(events.aclose(), timeout=CLOSE_SECONDS + 1)

    assert not model.closed
```

Замініть на:

```python

async def test_a_stream_closed_before_its_first_event_ends_its_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)

    await events.aclose()

    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_model_whose_close_never_ends_cannot_hold_the_stream() -> None:
    model = Model(hold=2, close_delay=60)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await asyncio.wait_for(events.aclose(), timeout=CLOSE_SECONDS + 1)

    assert not model.closed
```

**7.** Знайдіть:

```python

# --- Over HTTP -------------------------------------------------------------------


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_the_endpoint_streams_events_a_client_can_rebuild() -> None:
    app = create_app(streamer=streamer(Model()))
    async with client(app) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"X-Request-ID": str(REQUEST_ID)},
        )
```

Замініть на:

```python

# --- Over HTTP -------------------------------------------------------------------


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
        headers=AUTHORIZATION,
    )


async def test_the_endpoint_streams_events_a_client_can_rebuild() -> None:
    app = create_app(authenticator=TRUSTED, streamer=streamer(Model()))
    async with client(app) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"X-Request-ID": str(REQUEST_ID)},
        )
```

**8.** Знайдіть:

```python
) -> None:
    sources = {
        "denied": Source(error=AuthorizationDenied()),
        "slow": Source(wait=True),
    }
    app = create_app(
        streamer=None
        if answering is None
        else streamer(Model(), source=sources[answering], timeout_seconds=0.1)
    )
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": "Leave?"})

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_an_empty_question_is_refused_before_any_work() -> None:
    model = Model()
    app = create_app(streamer=streamer(model))
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": ""})

    assert response.status_code == 422
    assert model.calls == 0

```

Замініть на:

```python
) -> None:
    sources = {
        "denied": Source(error=AuthorizationDenied()),
        "slow": Source(wait=True),
    }
    app = create_app(
        authenticator=TRUSTED,
        streamer=None
        if answering is None
        else streamer(Model(), source=sources[answering], timeout_seconds=0.1),
    )
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": "Leave?"})

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_an_empty_question_is_refused_before_any_work() -> None:
    model = Model()
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": ""})

    assert response.status_code == 422
    assert model.calls == 0

```

**9.** Знайдіть:

```python
        "method": "POST",
        "scheme": "http",
        "path": "/v1/answer/stream",
        "raw_path": b"/v1/answer/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
```

Замініть на:

```python
        "method": "POST",
        "scheme": "http",
        "path": "/v1/answer/stream",
        "raw_path": b"/v1/answer/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", AUTHORIZATION["Authorization"].encode()),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
```

**10.** Знайдіть:

```python
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The model goes quiet after two pieces and would never finish alone.
    model = Model(hold=2)
    connection = Connection(leave_after=2)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
```

Замініть на:

```python
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The model goes quiet after two pieces and would never finish alone.
    model = Model(hold=2)
    connection = Connection(leave_after=2)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
```

**11.** Знайдіть:

```python
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_model_that_awaits_while_closing_is_closed_when_the_client_leaves(
    spec_version: str,
) -> None:
    model = Model(hold=2, close_delay=0.05)
    connection = Connection(leave_after=2)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
```

Замініть на:

```python
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_model_that_awaits_while_closing_is_closed_when_the_client_leaves(
    spec_version: str,
) -> None:
    model = Model(hold=2, close_delay=0.05)
    connection = Connection(leave_after=2)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
```

**12.** Знайдіть:

```python
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    model = Model()
    connection = Connection(leave_after=0)
    connection.left.set()
    app = create_app(streamer=streamer(model, source=source))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert source.cancelled
```

Замініть на:

```python
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    model = Model()
    connection = Connection(leave_after=0)
    connection.left.set()
    app = create_app(authenticator=TRUSTED, streamer=streamer(model, source=source))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert source.cancelled
```

**13.** Знайдіть:

```python
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stuck_close_cannot_hold_the_response_open() -> None:
    model = Model(hold=4, close_delay=60)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send),
        timeout=2 * CLOSE_SECONDS + 1,
    )


async def test_a_client_that_stops_reading_and_leaves_closes_the_model() -> None:
    # The server is stuck writing the second event when the client leaves.
    model = Model(hold=4)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=2
    )

    assert model.closed
```

Замініть на:

```python
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stuck_close_cannot_hold_the_response_open() -> None:
    model = Model(hold=4, close_delay=60)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send),
        timeout=2 * CLOSE_SECONDS + 1,
    )


async def test_a_client_that_stops_reading_and_leaves_closes_the_model() -> None:
    # The server is stuck writing the second event when the client leaves.
    model = Model(hold=4)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=2
    )

    assert model.closed
```

У `tests/test_stream_failures.py`:

**1.** Знайдіть:

```python
import psycopg.errors as pg
import pytest
from sqlalchemy import exc as database_errors
from starlette.types import Message

from evidence_fakes import MARKER
from knowledge_service.answering.events import (
    AnswerEventReader,
    AnswerEventWriter,
    InvalidEventStream,
    StreamOutcome,
)
```

Замініть на:

```python
import psycopg.errors as pg
import pytest
from sqlalchemy import exc as database_errors
from starlette.types import Message

from evidence_fakes import MARKER
from identity_fakes import READER, TRUSTED
from knowledge_service.answering.events import (
    AnswerEventReader,
    AnswerEventWriter,
    InvalidEventStream,
    StreamOutcome,
)
```

**2.** Знайдіть:

```python
    retry_after: str | None,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = Model()
    app = create_app(streamer=streamer(model, source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == status
    assert response.json()["code"] == code
```

Замініть на:

```python
    retry_after: str | None,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = Model()
    app = create_app(
        authenticator=TRUSTED, streamer=streamer(model, source=Source(error=error))
    )

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == status
    assert response.json()["code"] == code
```

**3.** Знайдіть:

```python
async def test_a_client_that_leaves_during_the_search_is_recorded_before_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    opening = asyncio.create_task(
        streamer(Model(), source=source).open(QUESTION, request_id=REQUEST_ID)
    )
    await asyncio.sleep(0)

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
```

Замініть на:

```python
async def test_a_client_that_leaves_during_the_search_is_recorded_before_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    opening = asyncio.create_task(
        streamer(Model(), source=source).open(
            QUESTION, reader=READER, request_id=REQUEST_ID
        )
    )
    await asyncio.sleep(0)

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
```

**4.** Знайдіть:

```python

async def test_a_client_that_leaves_mid_text_abandons_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    assert model.closed
```

Замініть на:

```python

async def test_a_client_that_leaves_mid_text_abandons_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    assert model.closed
```

**5.** Знайдіть:

```python
    ],
    ids=["program-limit", "wrong-state", "driver-misuse"],
)
async def test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after(
    error: Exception,
) -> None:
    app = create_app(streamer=streamer(Model(), source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert classify_failure(error) is FailureKind.INTERNAL
    assert response.status_code == 500
```

Замініть на:

```python
    ],
    ids=["program-limit", "wrong-state", "driver-misuse"],
)
async def test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after(
    error: Exception,
) -> None:
    app = create_app(
        authenticator=TRUSTED, streamer=streamer(Model(), source=Source(error=error))
    )

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert classify_failure(error) is FailureKind.INTERNAL
    assert response.status_code == 500
```

**6.** Знайдіть:

```python
async def test_a_client_that_leaves_as_the_headers_go_out_is_after_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    connection = LeavesOnHeaders(leave_after=1)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=5
    )

    assert connection.bodies == []
```

Замініть на:

```python
async def test_a_client_that_leaves_as_the_headers_go_out_is_after_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    connection = LeavesOnHeaders(leave_after=1)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=5
    )

    assert connection.bodies == []
```

**7.** Знайдіть:

```python
) -> None:
    caplog.set_level(logging.INFO)
    # In this key order nothing streams live; the whole text goes at the end.
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )
    events = await streamer(Model(final=final)).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "checking", "text_delta"]
    record = ended(caplog)
```

Замініть на:

```python
) -> None:
    caplog.set_level(logging.INFO)
    # In this key order nothing streams live; the whole text goes at the end.
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )
    events = await streamer(Model(final=final)).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )
    sent = [await anext(events), await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "checking", "text_delta"]
    record = ended(caplog)
```

**8.** Знайдіть:

```python
    partial: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    failing = Raising(database_down()) if finisher else None
    events = await streamer(Model(), finisher=failing).open(
        QUESTION, request_id=REQUEST_ID
    )
    sent: list[bytes] = []
    while not sent or names(sent)[-1] not in ("completion", "error"):
        sent.append(await anext(events))

    # It leaves before asking for more, while the stream waits at the end.
```

Замініть на:

```python
    partial: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    failing = Raising(database_down()) if finisher else None
    events = await streamer(Model(), finisher=failing).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )
    sent: list[bytes] = []
    while not sent or names(sent)[-1] not in ("completion", "error"):
        sent.append(await anext(events))

    # It leaves before asking for more, while the stream waits at the end.
```

**9.** Знайдіть:

```python
class FailingWorkflow:
    async def answer(self, question: Question, *, request_id: UUID) -> Any:
        raise ChatModelUnavailable(MARKER)


async def test_the_answer_endpoint_gives_the_same_retry_guidance() -> None:
    app = create_app(workflow=cast(AnswerWorkflow, FailingWorkflow()))

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
```

Замініть на:

```python
class FailingWorkflow:
    async def answer(self, question: Question, *, request_id: UUID) -> Any:
        raise ChatModelUnavailable(MARKER)


async def test_the_answer_endpoint_gives_the_same_retry_guidance() -> None:
    app = create_app(
        authenticator=TRUSTED, workflow=cast(AnswerWorkflow, FailingWorkflow())
    )

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
```

У `tests/test_stream_failures_integration.py`:

**1.** Знайдіть:

```python

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from test_streaming import (
    EVIDENCE,
```

Замініть на:

```python

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from identity_fakes import TRUSTED
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from test_streaming import (
    EVIDENCE,
```

**2.** Знайдіть:

```python
    """Find Evidence after one query, as a real source would."""

    def __init__(self, runtime: DatabaseRuntime, query: str) -> None:
        self.runtime = runtime
        self.query = query

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            await connection.execute(sa.text(self.query), {"marker": MARKER})
        return FoundEvidence(EVIDENCE, SIGNALS)


@pytest.fixture
```

Замініть на:

```python
    """Find Evidence after one query, as a real source would."""

    def __init__(self, runtime: DatabaseRuntime, query: str) -> None:
        self.runtime = runtime
        self.query = query

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            await connection.execute(sa.text(self.query), {"marker": MARKER})
        return FoundEvidence(EVIDENCE, SIGNALS)


@pytest.fixture
```

**3.** Знайдіть:

```python
    runtime: DatabaseRuntime, query: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Search with one failing query, then check what the client and log got."""
    caplog.set_level(logging.DEBUG)
    model = Model()
    source = cast(Source, DatabaseSource(runtime, query))
    app = create_app(streamer=streamer(model, source=source))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
```

Замініть на:

```python
    runtime: DatabaseRuntime, query: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Search with one failing query, then check what the client and log got."""
    caplog.set_level(logging.DEBUG)
    model = Model()
    source = cast(Source, DatabaseSource(runtime, query))
    app = create_app(authenticator=TRUSTED, streamer=streamer(model, source=source))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
```

У `tests/test_app.py`:

**1.** Знайдіть:

```python

import httpx
import pytest
from fastapi import FastAPI

from fakes import FakeChatModel, SlowChatModel
from knowledge_service.app import create_app
from knowledge_service.contracts import Answer, Question
from knowledge_service.errors import AuthorizationDenied
from knowledge_service.model import (
    ChatModelRateLimited,
    ChatModelTimeout,
```

Замініть на:

```python

import httpx
import pytest
from fastapi import FastAPI

from fakes import FakeChatModel, SlowChatModel
from identity_fakes import AUTHORIZATION, TRUSTED
from knowledge_service.app import create_app
from knowledge_service.contracts import Answer, Question
from knowledge_service.errors import AuthorizationDenied
from knowledge_service.model import (
    ChatModelRateLimited,
    ChatModelTimeout,
```

**2.** Знайдіть:

```python

def get_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    )


async def test_liveness_endpoint_returns_ok() -> None:
    app = create_app()
    async with get_client(app) as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


```

Замініть на:

```python

def get_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTHORIZATION,
    )


async def test_liveness_endpoint_returns_ok() -> None:
    app = create_app(authenticator=TRUSTED)
    async with get_client(app) as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


```

**3.** Знайдіть:

```python
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


async def test_answer_endpoint_returns_answer() -> None:
    workflow = AnswerWorkflow(model=FakeChatModel())
    app = create_app(workflow=workflow)
    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "What is the capital of France?"},
            headers={"X-Request-ID": REQUEST_ID},
        )
```

Замініть на:

```python
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


async def test_answer_endpoint_returns_answer() -> None:
    workflow = AnswerWorkflow(model=FakeChatModel())
    app = create_app(authenticator=TRUSTED, workflow=workflow)
    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "What is the capital of France?"},
            headers={"X-Request-ID": REQUEST_ID},
        )
```

**4.** Знайдіть:

```python
async def test_answer_endpoint_maps_failures(
    error: BaseException,
    status_code: int,
    code: str,
    message: str,
) -> None:
    app = create_app(workflow=error_workflow(error))

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A question"},
            headers={"X-Request-ID": REQUEST_ID},
```

Замініть на:

```python
async def test_answer_endpoint_maps_failures(
    error: BaseException,
    status_code: int,
    code: str,
    message: str,
) -> None:
    app = create_app(authenticator=TRUSTED, workflow=error_workflow(error))

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A question"},
            headers={"X-Request-ID": REQUEST_ID},
```

**5.** Знайдіть:

```python
        "message": message,
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_preserves_cancellation() -> None:
    app = create_app(workflow=error_workflow(asyncio.CancelledError()))

    async with get_client(app) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/v1/answer",
                json={"text": "A question"},
                headers={"X-Request-ID": REQUEST_ID},
            )


async def test_answer_endpoint_returns_workflow_unavailable_error() -> None:
    app = create_app()

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A question"},
            headers={"X-Request-ID": REQUEST_ID},
```

Замініть на:

```python
        "message": message,
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_preserves_cancellation() -> None:
    app = create_app(
        authenticator=TRUSTED, workflow=error_workflow(asyncio.CancelledError())
    )

    async with get_client(app) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/v1/answer",
                json={"text": "A question"},
                headers={"X-Request-ID": REQUEST_ID},
            )


async def test_answer_endpoint_returns_workflow_unavailable_error() -> None:
    app = create_app(authenticator=TRUSTED)

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A question"},
            headers={"X-Request-ID": REQUEST_ID},
```

**6.** Знайдіть:

```python
        "message": "Answer workflow is unavailable",
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_returns_validation_error() -> None:
    app = create_app(workflow=AnswerWorkflow(model=FakeChatModel()))

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": ""},
            headers={"X-Request-ID": REQUEST_ID},
```

Замініть на:

```python
        "message": "Answer workflow is unavailable",
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_returns_validation_error() -> None:
    app = create_app(
        authenticator=TRUSTED, workflow=AnswerWorkflow(model=FakeChatModel())
    )

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": ""},
            headers={"X-Request-ID": REQUEST_ID},
```

**7.** Знайдіть:

```python
    }


async def test_answer_endpoint_returns_correct_response_on_timeout() -> None:
    model = SlowChatModel()
    workflow = AnswerWorkflow(model=model, timeout_seconds=0.01)
    app = create_app(workflow=workflow)

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A Question"},
            headers={"X-Request-ID": REQUEST_ID},
```

Замініть на:

```python
    }


async def test_answer_endpoint_returns_correct_response_on_timeout() -> None:
    model = SlowChatModel()
    workflow = AnswerWorkflow(model=model, timeout_seconds=0.01)
    app = create_app(authenticator=TRUSTED, workflow=workflow)

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A Question"},
            headers={"X-Request-ID": REQUEST_ID},
```

**Перевірка:**

```bash
uv run pytest tests/test_streaming.py tests/test_stream_failures.py tests/test_app.py -q
```

Очікувано: `94 passed`, ті самі тести, що й раніше.

### Крок 6 — Крок із кодом: тести контексту і дверей

**Простими словами:** ці тести доводять обіцянку уроку з трьох боків. Unit-тести подають вибрані поля в зіставлення і перевіряють кожну відмову і кожен ID. Тести ендпоінтів надсилають справжні HTTP-запити з підробленими заголовками, зламаними тілами і поганими токенами і перевіряють, який читач, якщо взагалі, дійшов до пошуку. Integration-тести логінять трьох демо-користувачів через справжній Keycloak і перевіряють, що пошук ішов саме від їхнього імені.

| Файл | Що доводить |
|---|---|
| `tests/test_authorization_context.py` | ID стабільні, типізовані і прив'язані до видавця; відмови для видавця, клієнта, subject і груп; звичайні шляхи будь-якою писемністю зберігаються, дивні відкидаються і рахуються; розбір заголовка; тексту токена немає в помилках і логах |
| `tests/test_app_authentication.py` | Перевірений токен — це читач; підроблені заголовки і поля тіла нічого не змінюють; 401, 400, 503, 413 і 422 у правильному порядку; тіло не читається до ідентичності; health-перевірки не потребують токена |
| `tests/test_authentication_integration.py` | alice, bob і carol через справжній Keycloak стають саме своїм User і Groups; ID-токен чи відсутність токена нічого не читають |

Створіть `tests/test_authorization_context.py`:

```python
"""The Authorization Context: from a verified token's claims to typed IDs.

`Authenticator` is tested with a fake verifier: which tokens are genuine is
M07-T03's concern; here the claims are given and the mapping is checked.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import (
    IDENTITY_NAMESPACE,
    AuthenticationError,
    Authenticator,
    ContextPolicy,
    Unauthenticated,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import (
    IdentityProviderUnavailable,
    Rejection,
    TokenRejected,
    VerifiedToken,
)

ISSUER = "http://localhost:8180/realms/knowledge-service"
ALICE = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
POLICY = ContextPolicy(trusted_clients=frozenset({"open-webui"}))
MARKER = "SEEDED-SECRET-91c2"


def verified(**claims: object) -> VerifiedToken:
    """A token the verifier accepted, with these claims over alice's."""
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": ALICE,
        "azp": "open-webui",
        "groups": ["/engineering"],
        **claims,
    }
    payload = {name: value for name, value in payload.items() if value is not None}
    return VerifiedToken(
        subject=payload.get("sub", ALICE),
        key_id="key-1",
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        claims=payload,
    )


class Verifier:
    """Accepts the tokens it was given, rejects or fails on the rest."""

    def __init__(
        self,
        tokens: dict[str, VerifiedToken] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.tokens = tokens or {}
        self.error = error
        self.seen: list[str] = []

    async def verify(self, token: str) -> VerifiedToken:
        self.seen.append(token)
        if self.error is not None:
            raise self.error
        if token not in self.tokens:
            raise TokenRejected(Rejection.SIGNATURE)
        return self.tokens[token]


def defect(token: VerifiedToken) -> str:
    with pytest.raises(Unauthenticated) as raised:
        build_authorization_context(token, POLICY)
    return raised.value.reason


# --- Mapping claims to IDs ------------------------------------------------------------


def test_a_verified_token_names_its_user_and_groups() -> None:
    context = build_authorization_context(
        verified(groups=["/engineering", "/finance/payroll"]), POLICY
    )

    assert context == AuthorizationContext(
        user_id=user_id_for(ISSUER, ALICE),
        group_ids=frozenset(
            {
                group_id_for(ISSUER, "/engineering"),
                group_id_for(ISSUER, "/finance/payroll"),
            }
        ),
    )


def test_ids_are_stable_and_scoped_to_their_issuer() -> None:
    # Pinned: a change would orphan every stored grant for a User or Group.
    assert UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31") == IDENTITY_NAMESPACE
    assert user_id_for(ISSUER, ALICE) == user_id_for(ISSUER, ALICE)
    assert user_id_for(ISSUER, ALICE) != user_id_for(f"{ISSUER}-other", ALICE)
    assert group_id_for(ISSUER, "/a") != group_id_for("https://other.test", "/a")
    # A User and a Group with the same name are never the same UUID.
    assert user_id_for(ISSUER, "/a").value != group_id_for(ISSUER, "/a").value
    assert type(user_id_for(ISSUER, ALICE)) is UserId
    assert type(group_id_for(ISSUER, "/a")) is GroupId


def test_a_user_in_no_group_has_no_groups() -> None:
    for token in (verified(groups=None), verified(groups=[])):
        assert build_authorization_context(token, POLICY).group_ids == frozenset()


def test_a_group_listed_twice_is_one_group() -> None:
    context = build_authorization_context(
        verified(groups=["/engineering", "/engineering"]), POLICY
    )

    assert context.group_ids == {group_id_for(ISSUER, "/engineering")}


def test_the_token_text_itself_never_becomes_an_id() -> None:
    # Only the issuer and the names inside the verified claims count.
    one = build_authorization_context(verified(), POLICY)
    two = build_authorization_context(verified(), POLICY)

    assert one == two


# --- Refusals: untrusted, malformed, oversized ----------------------------------------


@pytest.mark.parametrize("client", ["account-console", "", None, ["open-webui"]])
def test_a_token_issued_to_another_client_gives_no_context(client: object) -> None:
    assert defect(verified(azp=client)) == "untrusted_client"


@pytest.mark.parametrize(
    "subject",
    ["", " alice", "alice\n", "al\x00ice", "al" + chr(0x200B) + "ice", "a" * 256],
)
def test_a_subject_that_is_not_a_printable_name_gives_no_context(
    subject: str,
) -> None:
    assert defect(verified(sub=subject)) == "subject"


@pytest.mark.parametrize(
    "groups",
    ["/engineering", {"/engineering": True}, [["/engineering"]], [42], [None]],
    ids=["string", "object", "nested", "number", "null"],
)
def test_a_groups_claim_that_is_not_a_list_of_names_gives_no_context(
    groups: object,
) -> None:
    assert defect(verified(groups=groups)) == "groups_malformed"


def hidden(*codes: int) -> str:
    """Characters that print as nothing, built from their code points."""
    return "".join(chr(code) for code in codes)


@pytest.mark.parametrize(
    "path",
    [
        "engineering",
        "/",
        "/engineering ",
        "/eng\nineering",
        "/eng" + hidden(0x202E) + "ineering",
        "/engineering" + hidden(0x200B),
        "/engineering" + hidden(0x3164),
        "/engineering" + hidden(0x2800),
        "/engineering" + hidden(0x034F),
        "/engineering" + hidden(0xFE0F),
        "/" + "a" * 255,
        "/finance~/payroll",
        "/finance~",
    ],
    ids=[
        "not-a-path",
        "root",
        "trailing-space",
        "newline",
        "bidi",
        "zero-width-space",
        "hangul-filler",
        "braille-blank",
        "grapheme-joiner",
        "variation-selector",
        "too-long",
        "escaped-slash",
        "tilde",
    ],
)
def test_a_group_path_that_is_not_plain_is_left_out_and_counted(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    context = build_authorization_context(
        verified(groups=[path, "/engineering"]), POLICY
    )

    assert context.group_ids == {group_id_for(ISSUER, "/engineering")}
    assert caplog.messages == ["group claim entries ignored: 1"]


@pytest.mark.parametrize(
    "path", ["/Data Science", "/finance/payroll", "/Інженерія", "/zzr18é", "/.."]
)
def test_plain_paths_in_any_script_are_kept(path: str) -> None:
    context = build_authorization_context(verified(groups=[path]), POLICY)

    assert context.group_ids == {group_id_for(ISSUER, path)}


def test_a_nested_group_and_a_name_with_a_slash_are_never_one_group() -> None:
    # With slashes escaped, Keycloak writes a top-level group "a/b" as
    # "/a~/b" and group "b" inside "a" as "/a/b". Only the second is kept.
    context = build_authorization_context(verified(groups=["/a/b", "/a~/b"]), POLICY)

    assert context.group_ids == {group_id_for(ISSUER, "/a/b")}


def test_group_paths_keep_their_case() -> None:
    assert group_id_for(ISSUER, "/Engineering") != group_id_for(ISSUER, "/engineering")


def test_a_token_without_an_issuer_gives_no_context() -> None:
    assert defect(verified(iss=None)) == "issuer"


def test_too_many_groups_give_no_context() -> None:
    at_limit = verified(groups=[f"/g{number}" for number in range(64)])
    over = verified(groups=[f"/g{number}" for number in range(65)])

    assert len(build_authorization_context(at_limit, POLICY).group_ids) == 64
    assert defect(over) == "groups_oversized"


@pytest.mark.parametrize(
    "settings",
    [
        {"trusted_clients": frozenset[str]()},
        {"trusted_clients": {"open-webui"}},
        {"trusted_clients": frozenset({"open-webui"}), "max_groups": 0},
    ],
)
def test_a_policy_that_trusts_no_client_or_allows_nothing_is_refused(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="must"):
        ContextPolicy(**settings)


# --- The Authorization header ---------------------------------------------------------


async def test_a_bearer_token_gives_the_context_of_its_claims() -> None:
    verifier = Verifier({"good-token": verified()})

    context = await Authenticator(verifier, POLICY).authenticate(["Bearer good-token"])

    assert context.user_id == user_id_for(ISSUER, ALICE)
    assert verifier.seen == ["good-token"]


@pytest.mark.parametrize(
    "header", ["Bearer good-token", "bearer good-token", "BEARER  good-token"]
)
async def test_the_scheme_is_case_insensitive_and_spaces_may_repeat(
    header: str,
) -> None:
    verifier = Verifier({"good-token": verified()})

    await Authenticator(verifier, POLICY).authenticate([header])

    assert verifier.seen == ["good-token"]


NONE = AuthenticationError.NONE
INVALID_REQUEST = AuthenticationError.INVALID_REQUEST


@pytest.mark.parametrize(
    ("headers", "reason", "error"),
    [
        ([], "missing", NONE),
        (["Basic YWxpY2U6c2VjcmV0"], "missing", NONE),
        (["good-token"], "missing", NONE),
        (["Bearer good-token", "Bearer good-token"], "repeated", INVALID_REQUEST),
        (["Bearer"], "malformed", INVALID_REQUEST),
        (["Bearer good-token extra"], "malformed", INVALID_REQUEST),
        (["Bearer good token"], "malformed", INVALID_REQUEST),
        (["Bearer good-token\n"], "malformed", INVALID_REQUEST),
        (["Bearer good-" + chr(0x17F) + "oken"], "malformed", INVALID_REQUEST),
    ],
    ids=[
        "missing",
        "basic",
        "no-scheme",
        "repeated",
        "no-token",
        "trailing-word",
        "space-inside",
        "newline",
        "non-ascii-letter",
    ],
)
async def test_a_header_that_is_not_one_bearer_token_is_refused_unverified(
    headers: list[str], reason: str, error: AuthenticationError
) -> None:
    verifier = Verifier({"good-token": verified()})

    with pytest.raises(Unauthenticated) as raised:
        await Authenticator(verifier, POLICY).authenticate(headers)

    assert (raised.value.reason, raised.value.error) == (reason, error)
    assert verifier.seen == []


async def test_a_token_the_verifier_rejects_is_refused_with_its_reason() -> None:
    verifier = Verifier(error=TokenRejected(Rejection.EXPIRED))

    with pytest.raises(Unauthenticated) as raised:
        await Authenticator(verifier, POLICY).authenticate(["Bearer old-token"])

    assert raised.value.reason == "expired"
    assert raised.value.__context__ is None


async def test_an_unavailable_identity_provider_is_not_an_unauthenticated_user() -> (
    None
):
    verifier = Verifier(error=IdentityProviderUnavailable("down"))

    with pytest.raises(IdentityProviderUnavailable):
        await Authenticator(verifier, POLICY).authenticate(["Bearer any-token"])


async def test_no_token_or_claim_text_reaches_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    verifier = Verifier(
        {
            MARKER: verified(sub=f"{MARKER}\n"),
            "client": verified(azp=MARKER),
        }
    )
    errors: list[BaseException] = []
    for header in (
        f"Bearer {MARKER}",
        "Bearer client",
        f"Basic {MARKER}",
        f"Bearer {MARKER}-rejected",
    ):
        with pytest.raises(Unauthenticated) as raised:
            await Authenticator(verifier, POLICY).authenticate([header])
        errors.append(raised.value)

    seen = " ".join(
        f"{error!s} {error!r} {error.__context__!r} {error.__cause__!r}"
        for error in errors
    )
    ignored = await Authenticator(
        Verifier({"groups": verified(groups=[f"/{MARKER}~"])}), POLICY
    ).authenticate(["Bearer groups"])

    assert ignored.group_ids == frozenset()
    assert MARKER not in seen + caplog.text
```

Створіть `tests/test_app_authentication.py`:

```python
"""Only the verifier's output can make a request's Authorization Context.

The app gets the real `Authenticator` over a fake verifier. Each test checks
what the Evidence source was asked as, so a reader that came from anywhere
but a verified token would show up here.
"""

import logging
from typing import Any, cast

import httpx
import pytest

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identity.context import Authenticator, group_id_for, user_id_for
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
    ALICE,
    ISSUER,
    MARKER,
    POLICY,
    Verifier,
    verified,
)
from test_streaming import QUESTION, REQUEST_ID, Model, Source, streamer

ALICE_CONTEXT = AuthorizationContext(
    user_id=user_id_for(ISSUER, ALICE),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)
TOKENS = {
    "alice-token": verified(),
    "outsider-token": verified(azp="account-console"),
    "crowded-token": verified(groups=[f"/g{n}" for n in range(65)]),
}


class Workflow:
    """Counts the ungrounded answers it was asked for."""

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: Any, *, request_id: Any) -> Any:
        self.calls += 1
        raise AssertionError("an unauthenticated request reached the workflow")


def app_with(
    source: Source, verifier: Verifier | None = None, *, authenticated: bool = True
) -> Any:
    authenticator = Authenticator(verifier or Verifier(TOKENS), POLICY)
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=authenticator if authenticated else None,
    )


def client(app: Any) -> httpx.AsyncClient:
    """A client that sends no Authorization header unless a test adds one."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def ask(app: Any, headers: Any = None, **body: Any) -> httpx.Response:
    async with client(app) as http:
        return await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text, **body},
            headers=headers,
        )


# --- Success: the reader is exactly what the token says -------------------------------


async def test_a_verified_token_is_the_reader_the_search_runs_as() -> None:
    source = Source()

    response = await ask(app_with(source), {"Authorization": "Bearer alice-token"})

    assert response.status_code == 200
    assert source.readers == [ALICE_CONTEXT]


async def test_headers_and_body_fields_cannot_choose_or_widen_the_reader() -> None:
    source = Source()
    spoofed = {
        "Authorization": "Bearer alice-token",
        "X-User-Id": "0f5e7d2a-0000-0000-0000-000000000000",
        "X-Groups": "/finance",
        "X-Forwarded-User": "carol",
        "X-Auth-Request-Groups": "/finance",
    }

    response = await ask(
        app_with(source),
        spoofed,
        user_id="carol",
        groups=["/finance"],
        reader={"user_id": "carol"},
    )

    assert response.status_code == 200
    assert source.readers == [ALICE_CONTEXT]


# --- Denials: no reader, and nothing runs ---------------------------------------------


@pytest.mark.parametrize(
    ("headers", "challenge"),
    [
        (None, "Bearer"),
        ({"Authorization": "Basic YWxpY2U6c2VjcmV0"}, "Bearer"),
        ({"Authorization": "Bearer forged-token"}, 'Bearer error="invalid_token"'),
        ({"Authorization": "Bearer outsider-token"}, 'Bearer error="invalid_token"'),
        ({"Authorization": "Bearer crowded-token"}, 'Bearer error="invalid_token"'),
    ],
    ids=["missing", "basic", "forged", "untrusted-client", "too-many-groups"],
)
async def test_a_request_without_a_usable_token_gets_401_and_no_search(
    headers: Any, challenge: str
) -> None:
    source = Source()

    response = await ask(app_with(source), headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    assert response.json()["code"] == "unauthenticated"
    assert source.readers == []


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "Bearer alice-token"), ("Authorization", "Bearer x")],
        [("Authorization", "Bearer")],
        [("Authorization", "Bearer alice-token extra")],
    ],
    ids=["repeated", "no-token", "trailing-word"],
)
async def test_a_malformed_authorization_header_is_a_400(
    headers: list[tuple[str, str]],
) -> None:
    source = Source()

    response = await ask(app_with(source), httpx.Headers(headers))

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_request"'
    assert source.readers == []


def raw_scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    """An ASGI request scope, for what an HTTP client would never send."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/answer/stream",
        "raw_path": b"/v1/answer/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


async def test_header_names_in_any_case_are_all_seen() -> None:
    # The ASGI spec asks servers for lower-case names; one that does not
    # must not hide a second Authorization header.
    source = Source()
    messages = [{"type": "http.request", "body": b'{"text": "Leave?"}'}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [
        (b"authorization", b"Bearer alice-token"),
        (b"Authorization", b"Bearer outsider-token"),
    ]
    await app_with(source)(raw_scope(headers), receive, send)

    assert sent[0]["status"] == 400
    assert source.readers == []


async def test_a_body_is_not_read_before_the_reader_is_known() -> None:
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 65_536, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app_with(Source())(raw_scope([]), receive, send)

    assert sent[0]["status"] == 401
    assert read == []


@pytest.mark.parametrize(
    ("verifier", "authenticated", "headers", "status"),
    [
        (None, True, {}, 401),
        (None, False, {"Authorization": "Bearer alice-token"}, 503),
        (
            Verifier(error=IdentityProviderUnavailable("down")),
            True,
            {"Authorization": "Bearer alice-token"},
            503,
        ),
    ],
    ids=["no-token", "no-authenticator", "provider-down"],
)
@pytest.mark.parametrize("body", [b"{not json", b"[", b'{"text": ""}'])
async def test_identity_is_decided_before_the_body_is_parsed(
    verifier: Verifier | None,
    authenticated: bool,
    headers: dict[str, str],
    status: int,
    body: bytes,
) -> None:
    app = app_with(Source(), verifier, authenticated=authenticated)

    async with client(app) as http:
        for path in ("/v1/answer", "/v1/answer/stream"):
            response = await http.post(
                path,
                content=body,
                headers={"Content-Type": "application/json", **headers},
            )
            assert response.status_code == status


async def test_an_authenticated_body_over_the_limit_is_a_413() -> None:
    source = Source()

    response = await ask(
        app_with(source),
        {"Authorization": "Bearer alice-token"},
        padding="x" * 70_000,
    )

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert source.readers == []


async def test_a_body_without_a_length_is_cut_off_at_the_limit() -> None:
    # No Content-Length: the limit must hold while the body streams in.
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 16_384, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"authorization", b"Bearer alice-token")]
    await app_with(Source())(raw_scope(headers), receive, send)

    assert sent[0]["status"] == 413
    assert len(read) == 5


async def test_an_authenticated_invalid_body_is_still_a_422() -> None:
    source = Source()

    async with client(app_with(source)) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": ""},
            headers={"Authorization": "Bearer alice-token"},
        )

    assert response.status_code == 422
    assert source.readers == []


class Broken:
    """An authenticator that fails in a way nobody planned for."""

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        raise KeyError(MARKER)


async def test_an_unexpected_authenticator_error_is_a_503_naming_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = Source()
    app = create_app(streamer=streamer(Model(), source=source), authenticator=Broken())

    response = await ask(app, {"Authorization": "Bearer alice-token"})

    assert response.status_code == 503
    assert MARKER not in response.text + caplog.text
    assert "authentication failed: KeyError" in caplog.messages
    assert source.readers == []


async def test_the_streamer_refuses_a_reader_that_is_not_a_context() -> None:
    with pytest.raises(InvalidAnswerRequest):
        await streamer(Model()).open(
            QUESTION,
            reader=cast(AuthorizationContext, ALICE_CONTEXT.user_id),
            request_id=REQUEST_ID,
        )


async def test_the_ungrounded_endpoint_needs_a_token_too() -> None:
    workflow = Workflow()
    app = create_app(
        workflow=cast(AnswerWorkflow, workflow),
        authenticator=Authenticator(Verifier(TOKENS), POLICY),
    )

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 401
    assert workflow.calls == 0


# --- When tokens cannot be checked: 503, never a guess --------------------------------


async def test_an_unavailable_identity_provider_is_a_503_with_retry_after() -> None:
    source = Source()
    verifier = Verifier(error=IdentityProviderUnavailable("down"))

    response = await ask(
        app_with(source, verifier), {"Authorization": "Bearer alice-token"}
    )

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "5"
    assert source.readers == []


@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
async def test_an_app_without_an_authenticator_answers_nobody(path: str) -> None:
    source = Source()
    app = app_with(source, authenticated=False)

    async with client(app) as http:
        response = await http.post(
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer alice-token"},
        )

    assert response.status_code == 503
    assert source.readers == []


async def test_health_checks_need_no_token() -> None:
    async with client(app_with(Source())) as http:
        live = await http.get("/health/live")
        ready = await http.get("/health/ready")

    assert (live.status_code, ready.status_code) == (200, 200)


# --- Nothing sensitive in the response or the log -------------------------------------


async def test_a_refusal_names_no_token_text_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    verifier = Verifier({MARKER: verified(sub=f"{MARKER}\n")})

    for header in (f"Bearer {MARKER}", f"Bearer {MARKER}x", f"Basic {MARKER}"):
        response = await ask(app_with(Source(), verifier), {"Authorization": header})
        assert response.status_code == 401
        assert MARKER not in response.text + str(response.headers)

    assert MARKER not in caplog.text
    reasons = [
        r.getMessage() for r in caplog.records if r.name == "knowledge_service.app"
    ]
    assert reasons == [
        "request not authenticated: subject",
        "request not authenticated: signature",
        "request not authenticated: missing",
    ]
```

Створіть `tests/test_authentication_integration.py`:

```python
"""The whole path against the local Keycloak (`make keycloak-up`).

A demo user logs in through the login form; their access token goes to the
app, and the Evidence source sees exactly their User and Groups. The tests
skip themselves when Keycloak or its generated secrets are missing.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from keycloak_flow import CLIENT_ID, ISSUER, SECRETS, local_secrets, log_in
from knowledge_service.access import AuthorizationContext
from knowledge_service.app import create_app
from knowledge_service.identity.context import (
    Authenticator,
    ContextPolicy,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import OidcVerifier
from test_streaming import QUESTION, Model, Source, streamer

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
USERS: dict[str, tuple[str, list[str]]] = {
    "alice": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01", ["/engineering"]),
    "bob": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a02", ["/finance"]),
    "carol": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03", []),
}
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in USERS}


@pytest.fixture
async def keycloak() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


def app_for(keycloak: httpx.AsyncClient, source: Source) -> Any:
    authenticator = Authenticator(
        OidcVerifier(ISSUER, AUDIENCE, keycloak),
        ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
    )
    return create_app(
        streamer=streamer(Model(), source=source), authenticator=authenticator
    )


async def ask(app: Any, token: str | None) -> httpx.Response:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        return await http.post(
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=headers
        )


@pytest.mark.parametrize("username", list(USERS))
async def test_each_demo_user_is_the_reader_their_token_names(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    source = Source()
    subject, paths = USERS[username]

    response = await ask(app_for(keycloak, source), tokens.access_token)

    assert response.status_code == 200
    assert source.readers == [
        AuthorizationContext(
            user_id=user_id_for(ISSUER, subject),
            group_ids=frozenset(group_id_for(ISSUER, path) for path in paths),
        )
    ]


async def test_an_id_token_or_no_token_reads_nothing(
    keycloak: httpx.AsyncClient,
) -> None:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        "alice",
        secrets["KS_ALICE_PASSWORD"],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    source = Source()
    app = app_for(keycloak, source)

    assert (await ask(app, tokens.id_token)).status_code == 401
    assert (await ask(app, None)).status_code == 401
    assert source.readers == []
```

**Перевірка:** спершу вузькі тести:

```bash
uv run pytest tests/test_authorization_context.py tests/test_app_authentication.py -q
uv run pytest -m integration tests/test_authentication_integration.py -q
```

Очікувано: `93 passed`, потім `4 passed` із запущеним Keycloak (`4 skipped` без нього).

### Крок 7 — Без коду: зламайте навмисно

У цьому кроці ви приберете правило, яке відкидає шляхи з `~`, побачите, що падають саме потрібні тести, і повернете його.

**Дія 1:** у `_plain_path` у `src/knowledge_service/identity/context.py` видаліть цей рядок:

```python
        and "~" not in path
```

**Дія 2:** запустіть `uv run pytest tests/test_authorization_context.py -q`. Очікувано: `4 failed, 57 passed`:

- `test_a_group_path_that_is_not_plain_is_left_out_and_counted[escaped-slash]`
- `test_a_group_path_that_is_not_plain_is_left_out_and_counted[tilde]`
- `test_a_nested_group_and_a_name_with_a_slash_are_never_one_group`
- `test_no_token_or_claim_text_reaches_errors_or_logs`

Третій показує саму атаку: токен зі шляхом `/a~/b` дав би своєму User групу `b` всередині `a`.

**Дія 3:** поверніть рядок і запустіть тести знову: `61 passed`.

**Дія 4:** порівняйте результат зі своїми прогнозами у файлі evidence і запишіть, які з них не справдилися.

### Крок 8 — Без коду: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все досі працює, і записує результат. Тут важливе трасування в моделі загроз. Інваріант 1 називав `M07-T04` майбутнім власником, а `make docs-check` не приймає виконану задачу як власника. Тому трасування тепер називає тести цього уроку.

**Дія 1:** оновіть два документи.

У `docs/architecture.md`:

**1.** Знайдіть:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Замініть на:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Знайдіть:

```markdown
sequenceDiagram
    participant User
    participant API as API and AnswerStreamer
    participant Retrieval
    participant DB as PostgreSQL
    participant Model
    User->>API: Question
    API->>Retrieval: Question + Authorization Context
    Retrieval->>DB: lexical and vector search with ACL predicates
    DB-->>Retrieval: eligible Candidates only
    Retrieval-->>API: ranked Candidates and their signals
    API->>API: build_evidence under opaque Evidence IDs, assess_evidence
    API->>API: allocate_context and render_prompt allowing only these IDs
```

Замініть на:

```markdown
sequenceDiagram
    participant User
    participant API as API and AnswerStreamer
    participant Retrieval
    participant DB as PostgreSQL
    participant Model
    User->>API: Question with a bearer token
    API->>API: Authenticator verifies the token (keys from the IdP's JWKS) and builds the Authorization Context, or answers 401 before reading the Question
    API->>Retrieval: Question + Authorization Context
    Retrieval->>DB: lexical and vector search with ACL predicates
    DB-->>Retrieval: eligible Candidates only
    Retrieval-->>API: ranked Candidates and their signals
    API->>API: build_evidence under opaque Evidence IDs, assess_evidence
    API->>API: allocate_context and render_prompt allowing only these IDs
```

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown
## Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |
```

Замініть на:

```markdown
## Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request; built only by `Authenticator` from a verified token |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |
```

**2.** Знайдіть:

```markdown
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token. `OidcVerifier` verifies one; the endpoints call it from M07-T04.
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
```

Замініть на:

```markdown
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token. Both answer endpoints accept only a request whose bearer token `OidcVerifier` verifies, and `Authenticator` builds its Authorization Context from that token's claims alone.
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token, checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
```

**3.** Знайдіть:

```markdown
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Nothing yet in the endpoints: `OidcVerifier` exists, but neither answer endpoint calls it | The Authorization Context from verified tokens only (M07-T04) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew | The endpoints use it (M07-T04) |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants`, `M07-T04` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- The API checks no token yet: `OidcVerifier` exists, but until M07-T04 the reader is whoever the Evidence source was built for.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
```

Замініть на:

```markdown
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Both answer endpoints need a bearer token before the body is read; no other header, cookie, or body field names the reader; bodies over 64 KiB are refused; without an authenticator the endpoints answer 503 | Identity-aware rate limits (M09-T10) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew; `Authenticator` calls it on every answer request | None |
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | Source group normalization (M07-T06) |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; Source groups get their own mapping in M07-T06. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
```

**Дія 2:** запустіть перевірки по черзі, із запущеним Keycloak:

```bash
uv run pytest tests/test_authorization_context.py tests/test_app_authentication.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. В еталонному запуску `make test-unit` дав 2551 passed, на 94 більше, ніж після M07-T03. `make test-integration` дав 416 passed, на 4 більше.

**Дія 3:** заповніть файл evidence:

- **Design:** звідки беруться ID, правила для полів, порядок автентифікації і читання тіла, коди статусів.
- **Verification:** команди й кількості, навмисна поломка та відомі обмеження з розділу «Перш ніж почати».
- **Review:** підсумок рев'ю з розділу «Перш ніж почати».
- **Decision:** чому ID беруться з назв без таблиці; чому одна дивна група коштує тільки себе; чому тіло чекає на ідентичність. Додайте щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск або рев'ю показали Y. Отже, Z хибне».
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу як виконану, додайте рядок M07-T04 у `docs/curriculum/PROGRESS.md`, укажіть наступною задачею M07-T05 і зробіть коміт із повідомленням `m07-t04: build the authorization context`.

## Запитання для рефлексії

1. Код, що викликає `admit`, отримує читача і Question. Про що йому більше не треба думати, порівняно з читанням ID користувача із заголовка?
2. Згодом хтось захоче пришвидшити відповіді і кешувати Evidence за текстом Question. Яка частина Authorization Context має входити в ключ кешу, і що витече, якщо не входитиме?
3. Хтось додає другий клієнт Keycloak, `admin-ui`, і дає йому аудиторію API. Що сьогодні станеться із запитами його користувачів, і що довелося б свідомо змінити, щоб їх пустити?
4. Ви вважаєте, що відкинути дивну групу безпечніше, ніж відхилити весь токен. Що б ви подивилися в логах або який випадок перевірили, щоб дізнатися, чи користувачі тихо не втрачають потрібних їм груп?
5. Carol не входить у жодну групу. Напишіть, що містить її Authorization Context і які Documents їй може повернути пошук.

## Контрольний список завершення

- [ ] Файл evidence містить інваріант і ваші прогнози, написані до змін.
- [ ] Keycloak екранує слеші в назвах груп, і тест конфігурації це закріплює.
- [ ] `knowledge_service.identity.context` перетворює перевірений токен на `UserId` і `GroupId`, відхиляє недовірених клієнтів, погані subject і зіпсовані чи завеликі поля груп, а дивні шляхи відкидає.
- [ ] Обидва answer-ендпоінти автентифікують до читання тіла, читають щонайбільше 64 КіБ і відповідають 400, 401, 413 або 503 так, як описують RFC 6750 і урок.
- [ ] Потокова відповідь передає читача джерелу Evidence; більше ніщо його не називає.
- [ ] Наявні тести проходять із автентифікатором-замінником (94).
- [ ] `tests/test_authorization_context.py` і `tests/test_app_authentication.py` проходять (93), а `tests/test_authentication_integration.py` проходить проти Keycloak (4).
- [ ] Ви зробили навмисну поломку, побачили чотири тести, що впали, і повернули все назад.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують Authorization Context, а трасування називає нові тести.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим списком.
