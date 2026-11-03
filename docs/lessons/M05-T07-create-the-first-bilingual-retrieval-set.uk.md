# M05-T07 — Створити перший двомовний набір для оцінювання пошуку

Англійська версія: [M05-T07-create-the-first-bilingual-retrieval-set.md](M05-T07-create-the-first-bilingual-retrieval-set.md)

Вихідне завдання: [M05-T07](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T07-create-the-first-bilingual-retrieval-set.md)

## Що ви зробите

Досі кожен тест пошуку використовував кілька речень, написаних прямо в самому тесті. Цей урок будує першу справжню мірку: невеликий набір документів англійською та українською і список запитань, де кожне називає точний уривок, що на нього відповідає. Потім скрипт ставить кожне запитання і звітує, на якому місці опинився правильний уривок. Наступні уроки проганятимуть той самий набір через нові способи пошуку й порівнюватимуть числа.

- **Корпус** (corpus) із семи коротких документів Markdown: три англійською, три українською і один закритий документ, який не може бачити жоден звичайний читач.
- **Файл випадків** `cases.json` із 16 запитаннями. Кожне називає уривок, що на нього відповідає, за шляхом документа і заголовком. Деякі ще називають закритий уривок, який ніколи не можна повертати.
- Модуль `evaluation_dataset.py`. Він завантажує файл випадків і відхиляє зламаний, звіряє кожен названий уривок із корпусом без бази даних і проганяє весь набір на порожній базі даних.
- Скрипт `run_retrieval_dataset.py`, який робить або лише перевірку, або повний прогін і пише звіт із місцями для кожного випадку та кожного зрізу.

Випадки покривають усе, що вимагає завдання: з англійської на англійську, з української на українську, обидва міжмовні напрями, точні ідентифікатори на кшталт `EXP-2041` і українські закінчення слів (морфологію).

| Завдання вимагає | Де це відбувається |
|---|---|
| Невеликий корпус англійською та українською | Крок 2: сім файлів у `datasets/bilingual-seed/corpus/` |
| Випадки для англійської, української, ідентифікаторів, морфології та обох міжмовних напрямів | Крок 3: `cases.json`; крок 4: правило покриття відхиляє файл без потрібного зрізу |
| Кожен випадок називає релевантні документи та фрагменти | Крок 3: кожен випадок перелічує уривки `relevant` за шляхом документа і шляхом заголовків |
| Набір перевірено | Крок 4: `verify_corpus` ріже корпус на фрагменти так само, як ingestion, і перевіряє кожне посилання |
| Скрипт проганяє набір детерміновано | Крок 5: `scripts/run_retrieval_dataset.py`; крок 8: два прогони дають однаковий звіт |
| Успіх, найризикованіший крайній випадок, одна типізована помилка або відмова | Кроки 6–7: базовий набір завантажується і проганяється; закритий уривок ніколи не повертається; зламані файли кидають `InvalidDataset`, а пошук, що «протікає», кидає `IneligibleChunkRetrieved` |
| Авторизацію збережено, у телеметрії немає чутливого тексту | Крок 7: закритий документ збережено, але його ніколи не знаходять, а його текст-маркер відсутній у звіті та логах |

У цьому уроці є програмування: кроки 4–7 пишуть Python. Кроки 2 і 3 створюють файли даних (Markdown і JSON) з наданим точним текстом. Кроки 1, 8 і 9 не стосуються коду.

Терміни цього уроку:

- **Випадок оцінювання (Evaluation Case):** одне запитання, уривки, що на нього відповідають, і позначки, які кажуть, що це за запитання. Так його називає глосарій проєкту.
- **Набір для оцінювання (Evaluation Dataset):** іменована, версійована збірка перевірених випадків. Тут він зветься `bilingual-seed`, версія 1.
- **Корпус (corpus):** документи, у яких шукають випадки. Незмінний корпус означає, що кожен прогін шукає в тому самому тексті.
- **Релевантний (relevant):** уривок, що відповідає на запитання. Добрий пошук повертає його близько до верху.
- **Недоступний (ineligible):** уривок, який читачеві не можна бачити. Його ніколи не можна повертати, хоч як добре він підходить. Релевантність і доступ — окремі питання: ідеальний збіг у приватному документі все одно заборонений.
- **Зріз (slice):** група випадків зі спільною позначкою, наприклад «запитання українською, відповідь англійською» (`uk->en`). Результати рахують окремо для кожного зрізу, бо одна загальна сума ховає слабку мову.
- **Напрям (direction):** мова запитання і мова відповіді, записані як `en->uk` для англійського запитання з відповіддю в українському документі.
- **Морфологія (morphology):** як слово змінює закінчення, наприклад «лікарняний», «лікарняного», «лікарняні». Пошук, що порівнює точні слова, пропускає інше закінчення.
- **Детермінований (deterministic):** той самий вхід завжди дає той самий результат, як калькулятор. Прогін, що залежить від випадковості або платної моделі, таким не є.

## Перш ніж почати

Цей розділ пояснює рішення, що стоять за файлами, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже є.** `DirectorySource` читає теку з файлами, `normalize` розбирає кожен із них, а `chunk_by_structure` ріже розібраний документ на фрагменти (Chunk). `synchronize_source` проходить увесь цей шлях і зберігає результат. `retrieve_hybrid` з M05-T06 опитує пошуки і зливає їхні списки. Кожен кандидат (Candidate), якого він повертає, несе `source_id`, `external_id` (шлях до файлу в теці) і `heading_path`.
- **Чому випадки називають уривки за шляхом і заголовком, а не за ID.** ID фрагмента — нове випадкове значення при кожному завантаженні, тож його не можна записати у файл. Пара «шлях до файлу плюс шлях заголовків» стабільна й читається легко, як поштова адреса замість номера посилки. Наприклад: `public/annual-leave.md` із заголовком `Annual leave policy > Days per year`. `heading_path` — це ланцюжок заголовків над фрагментом, з'єднаних через ` > `.
- **Чому кожен розділ — один фрагмент.** Структурне різання починає новий фрагмент на заголовку, лише коли попередній фрагмент має щонайменше 64 токени. Короткий розділ злився б із сусіднім, і дві відповіді мали б один заголовок. Тому кожен розділ корпусу, включно зі вступом, має щонайменше 64 токени і менше ніж 256. Українські слова дають менше токенів на рядок, ніж англійські, тож українські розділи трохи довші. Перевірка з кроку 4 ловить будь-який розділ, що злився.
- **Чому перевірка працює без бази даних.** `verify_corpus` читає і ріже корпус тими самими функціями, що й ingestion, а потім переконується, що кожен названий уривок існує рівно один раз. Їй не потрібні ні база даних, ні мережа, тож вона може запускатися на кожному коміті. Випадок, що називає уривок, якого різання ніколи не створює, ловиться ще до будь-яких вимірювань.
- **Хто що читає.** Корпус має дві теки. Файли з `public/` зберігаються як доступні всім. Файли з `restricted/` зберігаються взагалі без читачів — так проєкт сьогодні робить із будь-яким непублічним документом. Кожен випадок ставиться від імені одного звичайного працівника, `READER`. Список `ineligible` у випадку називає закриті уривки, що добре підходять до його запитання. Якщо будь-який пошук колись поверне закритий фрагмент, прогін зупиняється з `IneligibleChunkRetrieved`.
- **Чому прогону потрібна порожня база даних.** Прогін сам завантажує корпус із фіксованими ID джерел (Source). Інші документи в тій самій базі змагалися б за місця і змінили б числа. Тому прогін відмовляється працювати з базою, де вже є хоч одне джерело, ще до того, як щось запише. Це повторює бенчмарк з M04-T11.
- **Чому за замовчуванням працює лише лексичний пошук.** Векторний пошук потребує платної моделі ембедингів і дає трохи інші числа, коли модель змінюється. Пошуку за словами не потрібне ні те, ні інше, тож він — детермінована точка відліку. `run_dataset` приймає й інші гілки, але зберігає фрагменти без векторів: ембединги він не публікує. Векторна гілка, передана сьогодні, не знайшла б поточного простору ембедингів (Embedding Space), тож M05-T08 спершу має додати крок ембедингу після завантаження.
- **Що містить звіт.** Для кожного випадку — місце кожного релевантного уривка серед перших 10, або `missed`. Потім для кожного зрізу — кількість випадків, де знайдено хоча б один релевантний уривок. Recall@k і MRR, стандартні оцінки, з'являться в M05-T08; цей урок залишає їм сирі місця, яких вони потребують. Звіт ніколи не містить ні запитання, ні тексту документа, лише ID випадків і числа.
- **Очікуйте багато промахів.** Пошук за словами вимагає, щоб кожне слово запитання, крім англійських стоп-слів на кшталт "the", було в уривку, а пошук `simple` для української не зводить слова до основ. Тож більшість природних запитань промахуються, а кожне міжмовне запитання промахується. Саме цю точку відліку набір і має показати, це не помилка. Не переписуйте запитання, щоб воно пройшло: набір, підігнаний під поточний пошук, уже не зможе виміряти наступний.
- **Відомі обмеження.** Однакові оцінки пошуку впорядковуються за ID фрагмента, який новий при кожному завантаженні, тож два уривки з точно однаковою оцінкою могли б мінятися місцями між прогонами. Файл, змінений між перевіркою і завантаженням, повторно не перевіряється: файл, який не вдається розібрати, зупиняє прогін, але змінений заголовок лише покаже `missed`. Прогін, що впав після запису корпусу, залишає базу використаною, тож наступний прогін починайте на свіжій. Для всіх випадків використовується один читач, тож дозволи окремих користувачів тут не перевіряються. Шістнадцять випадків — це зародок, а не статистично сильна вибірка; ціль у `docs/evaluation.md` — близько 200.

## Покроковий план

### Крок 1 — Без програмування: запишіть інваріант і свої передбачення

Цей крок ще до появи будь-якого файлу записує, що набір має гарантувати і що ви очікуєте побачити в першому прогоні. Передбачення, записані заздалегідь, роблять подальше порівняння чесним.

Створіть `docs/evidence/M05-T07-bilingual-retrieval-set.md` із цим каркасом, а потім своїми словами заповніть передбачення:

```markdown
# M05-T07 — Bilingual retrieval set evidence

## Invariant

Every case in `datasets/bilingual-seed/cases.json` names at least one
relevant Chunk in `public/` by Document path and heading path, and each
reference matches exactly one Chunk when the corpus is chunked as ingestion
does. Every required slice (four language directions, identifiers,
morphology) has at least two cases, and at least one case names ineligible
evidence. A run asks every case as one ordinary reader on an empty
database; a Chunk from `restricted/` is never returned, and the report
holds only case IDs, labels, and ranks.

## Expected failing check

Before the change, `uv run pytest tests/test_evaluation_dataset.py` fails
to import `knowledge_service.retrieval.evaluation_dataset`.

## Predictions

- Which English questions will word search find, and which will it miss?
- Will any cross-language question (`en->uk`, `uk->en`) be found?
- Will "Хто оплачує лікарняні?" find the section "Оплата лікарняного"?
- Will the identifier question in Ukrainian, "Що означає помилка VPN-4031?",
  find the English section about VPN-4031?
- Can the restricted executive leave section ever appear?

## Design

## Verification

## Reflection
```

Перевірка: файл існує, і п'ять пунктів передбачень мають ваші відповіді. Якщо не впевнені, як пошук за словами обробляє запитання, ще раз перечитайте «Очікуйте багато промахів» у розділі «Перш ніж почати».

### Крок 2 — Без програмування: напишіть корпус

Цей крок створює документи, у яких шукатимуть випадки: три англійські сторінки, три українські та одну закриту. Це короткі вигадані правила компанії, тож нічого приватного в репозиторій не потрапляє.

- **Теки:** `datasets/bilingual-seed/corpus/public/` і `datasets/bilingual-seed/corpus/restricted/`.
- **Форма:** кожен файл має один заголовок `#`, один вступний абзац і два-три розділи `##`. Кожен блок містить від 64 до 256 токенів, тож кожен стає рівно одним фрагментом.
- **Закладені деталі:** ідентифікатори `EXP-2041`, `VPN-4031` і `ІТ-0457`, які пошук точних слів має знаходити. Українське слово «лікарняний» трапляється в кількох формах, що перевіряє морфологію. Закрита сторінка повторює маркер `CONFIDENTIAL-SEED-7731`, який тести шукають там, де його ніколи не має бути.
- **Навмисний збіг тем:** закрита сторінка — про щорічну відпустку, як і `annual-leave.md`, тож вона добре підходить до запитань про відпустку. Саме це робить її справжньою перевіркою доступу.

Копіюйте кожен файл точно: змінене слово змінює кількість токенів і місця.

`datasets/bilingual-seed/corpus/public/annual-leave.md`:

```markdown
# Annual leave policy

This policy explains how much paid time off every employee receives, how
to plan it with the team, and what happens to days that are not used by
the end of the year. It applies to permanent and fixed-term employees in
every office, from the first day of work.
Questions that this page does not answer go to the people team, who
update the policy every January.

## Days per year

Every employee receives twenty-four working days of paid annual leave per
calendar year. Employees who join or leave during the year receive a share
of these days in proportion to the months they worked. Public holidays do
not count against the allowance, and a holiday that falls inside a planned
vacation simply extends it by one day.

## Requesting leave

Request annual leave in the HR portal at least two weeks before the first
day away. Choose the dates, name a colleague who covers urgent work, and
submit the request to your manager. A manager answers within three working
days. A request for more than ten consecutive days needs an extra approval
from the head of the department, because it affects project plans.

## Carrying days over

Unused days can be carried over to the next year, up to a maximum of five
days. Carried-over days must be used before the end of March, otherwise
they expire without payment. Days above the limit are lost on the first of
January, so plan the remaining vacation with your manager in the autumn and
book it early.
```

`datasets/bilingual-seed/corpus/public/expense-reports.md`:

```markdown
# Expense reports

Employees who pay for work costs with their own money are reimbursed
through an expense report. This page describes the deadline, the receipts
the finance team needs, and the codes that the reporting system uses when
something is wrong with a submitted report.
Questions about a specific payment go to the finance team, who answer
them in the order they arrive.

## Submission deadline

Submit every expense report within thirty days of the purchase. Attach a
photo or a scan of each receipt, and add a short note that explains the
business purpose. Reports submitted after the deadline are still reviewed,
but they need written approval from your manager, and the payment moves to
the next monthly payroll run instead of the current one.

## Error code EXP-2041

The reporting system shows error EXP-2041 when a receipt is missing or the
image cannot be read. Open the rejected report, upload a sharper image of
the receipt, and submit it again. If the original receipt is lost, fill in
the missing receipt statement instead, sign it, and ask your manager to
confirm the amount before the report goes back to finance.
```

`datasets/bilingual-seed/corpus/public/vpn-access.md`:

```markdown
# Remote access with the VPN

The company network can be reached from home or from a hotel only through
the virtual private network. This guide explains how to connect for the
first time and what to do when the connection fails with one of the
common error codes that the client shows.
When none of these steps help, the service desk can check the connection
logs together with you.

## Connecting for the first time

Install the VPN client from the software catalog on your laptop, then sign
in with your work account. The first connection asks you to approve a
request in the authenticator app on your phone. Keep the app installed: the
client asks for a new approval every twelve hours and after every restart,
and it disconnects when the approval times out.

## Error VPN-4031

The client shows error VPN-4031 when your device certificate has expired.
Certificates are valid for one year and renew automatically only while the
laptop connects to the office network at least once a month. To fix the
error, connect the laptop to the office network or open a ticket with the
service desk, which can issue a new certificate remotely.
```

`datasets/bilingual-seed/corpus/public/likarnianyi.md`:

```markdown
# Лікарняний

Ця сторінка пояснює, що робити працівнику, який захворів, кого треба
попередити в перший день хвороби, які документи потрібні для оплати
пропущених днів і як повернутися до роботи після одужання без зайвих
затримок і непорозумінь з бухгалтерією.
Якщо у вас залишилися питання, напишіть у відділ кадрів, який відповідає
протягом одного робочого дня.
Відділ кадрів зберігає ці правила і оновлює їх щороку в січні.

## Перший день хвороби

Якщо ви захворіли, повідомте керівника до початку робочого дня телефоном
або повідомленням у робочому чаті. Позначте відсутність у кадровому
порталі, щоб колеги бачили, що вас немає. Перші три дні хвороби можна
оформити без довідки, але лише двічі на рік. Якщо хвороба триває довше,
зверніться до сімейного лікаря і попросіть відкрити електронний
лікарняний.
Поки лікарняний відкритий, не виходьте на роботу і не відповідайте на
робочі листи.

## Оплата лікарняного

Бухгалтерія оплачує лікарняний після того, як лікар закриє електронний
лікарняний лист. Перші п'ять днів оплачує компанія, а решту днів оплачує
фонд соціального страхування. Сума залежить від вашого страхового стажу:
за стажу понад вісім років оплачується повна середня зарплата. Виплата
надходить разом із найближчою зарплатою.
Якщо сума здається неправильною, напишіть у бухгалтерію і додайте номер
лікарняного листа, щоб його швидко знайшли.
```

`datasets/bilingual-seed/corpus/public/obladnannia.md`:

```markdown
# Робоче обладнання

Компанія видає кожному працівнику ноутбук, гарнітуру та інше обладнання,
потрібне для роботи. Ця сторінка пояснює, як замовити нову техніку, як
повідомити про поломку і що робити з обладнанням, коли працівник
звільняється або переходить в інший відділ компанії.
Якщо у вас залишилися питання, напишіть у службу підтримки, яка
відповідає протягом одного робочого дня.
Служба підтримки зберігає ці правила і оновлює їх щороку в січні.

## Замовлення ноутбука

Новий ноутбук замовляють через форму ІТ-0457 у каталозі послуг. У формі
вкажіть посаду, потрібну операційну систему та адресу доставки. Керівник
погоджує замовлення, після чого служба підтримки готує ноутбук протягом
п'яти робочих днів. Ноутбук замінюють новим не частіше ніж раз на три
роки, якщо він справний і не пошкоджений.
Якщо ноутбук потрібен терміново, напишіть про це в коментарі до форми,
і служба підтримки підготує його раніше.

## Поломка обладнання

Якщо ноутбук або інше обладнання зламалося, створіть звернення до служби
підтримки і опишіть, що сталося. Не намагайтеся ремонтувати техніку
самостійно і не віддавайте її в сторонній сервіс, бо це скасовує
гарантію. На час ремонту служба підтримки видає підмінний ноутбук, щоб ви
могли працювати далі.
Про крадіжку або втрату ноутбука повідомте в той самий день, щоб служба
підтримки встигла заблокувати доступ до робочих даних.
```

`datasets/bilingual-seed/corpus/public/vidriadzhennia.md`:

```markdown
# Службові відрядження

Ця сторінка описує, як погодити службове відрядження, скільки компанія
платить на день за кордоном і в Україні та як звітувати про витрати після
повернення. Правила однакові для всіх працівників, які їдуть у справах
компанії на один день або довше.
Якщо у вас залишилися питання, напишіть у бухгалтерію, яка відповідає
протягом одного робочого дня.
Бухгалтерія зберігає ці правила і оновлює їх щороку в січні.

## Добові

За кожен день відрядження в межах України компанія виплачує добові у
розмірі шестисот гривень. За кордоном добові становлять п'ятдесят євро на
день незалежно від країни. Добові покривають харчування та дрібні витрати,
тому чеки за них не потрібні. Проживання і квитки оплачуються окремо за
фактичними чеками, які треба зберегти до кінця звітного місяця.
Якщо відрядження скасували, повідомте бухгалтерію, щоб вона не
нараховувала добові за ці дні.

## Звіт про відрядження

Протягом п'яти робочих днів після повернення заповніть звіт про
відрядження у фінансовій системі. Додайте скани квитків, рахунок за
готель і посадковий талон, якщо ви летіли літаком. Звіт погоджує
керівник, а потім бухгалтерія. Без погодженого звіту наступне відрядження
оформити не можна, тому не відкладайте його до наступного місяця.
Якщо звіт повернули на доопрацювання, виправте його протягом двох
робочих днів і надішліть повторно.
```

`datasets/bilingual-seed/corpus/restricted/executive-leave.md`:

```markdown
# Executive annual leave

This restricted page describes the annual leave terms for members of the
executive board. It must never appear in search results for ordinary
employees, even when their question about annual leave matches it well.
Marker for leak checks: CONFIDENTIAL-SEED-7731.
Only the people team and the board office may read it, and copies must
not be shared in chats or email.

## Executive days per year

Members of the executive board receive thirty-two working days of paid
annual leave per calendar year, and they can carry over up to fifteen
unused days of annual leave to the next year. Requests for annual leave
by board members are approved by the chair of the board instead of a
manager. Marker for leak checks: CONFIDENTIAL-SEED-7731.
```

Імена файлів складаються з малих латинських літер, цифр і дефісів, бо завантажувач із кроку 4 приймає у випадку лише такі шляхи. Тому українські назви записано транслітом.

Перевірка: ця команда друкує кожен фрагмент із кількістю токенів і шляхом заголовків. Кожен заголовок має з'явитися рівно один раз, і жодне число не має бути меншим за 64.

```bash
uv run python - <<'EOF'
import asyncio
from pathlib import Path
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import normalize
from knowledge_service.ingestion.sources import SourceAccess

async def main() -> None:
    for folder in ("public", "restricted"):
        root = Path("datasets/bilingual-seed/corpus") / folder
        page = await DirectorySource(root, access=SourceAccess(public=True)).fetch_page(
            checkpoint=None, page_size=100
        )
        for record in page.records:
            document = normalize(record, source_id=SourceId.new())
            print(folder, record.external_id, document.language.language)
            for chunk in chunk_by_structure(document.parsed):
                print("   ", chunk.token_count, chunk.heading_path)

asyncio.run(main())
EOF
```

Очікувано: 21 фрагмент. Наприклад, `annual-leave.md` дає чотири, із заголовками `Annual leave policy`, `Annual leave policy > Days per year`, `Annual leave policy > Requesting leave` і `Annual leave policy > Carrying days over`. Кожен англійський файл розпізнано як `en`, кожен український — як `uk`.

### Крок 3 — Без програмування: напишіть файл випадків

Цей крок записує 16 запитань і для кожного — уривок, що на нього відповідає. Цей файл і є справжнім змістом набору; код наступних кроків лише перевіряє і проганяє його.

Кожен випадок має такі поля:

| Поле | Що містить |
|---|---|
| `id` | Коротка унікальна назва з малих слів, з'єднаних дефісами. Звіти показують лише її. |
| `question` | Текст запитання, як його набрала б людина. |
| `question_language`, `evidence_language` | `en` або `uk`. Разом вони дають напрям, наприклад `uk->en`. |
| `query_class` | `plain`, `identifier` (питає про код на кшталт `EXP-2041`) або `morphology` (вживає іншу форму слова, ніж у тексті). |
| `relevant` | Один або кілька уривків, що відповідають: `document` (тека і файл) і `heading` (повний шлях заголовків). |
| `ineligible` | Необов'язкове. Закриті уривки, що добре підходять, але ніколи не мають повертатися. |

Випадки за зрізами:

| Зріз | Випадки |
|---|---|
| `en->en` | 6: три звичайні, два ідентифікатори, один англійський випадок морфології ("renewed" проти "renew") |
| `uk->uk` | 5: два звичайні, один ідентифікатор, два випадки морфології («лікарняні», «ноутбуки») |
| `en->uk` | 2: добові за кордоном, оплата лікарняного |
| `uk->en` | 3: щорічна відпустка, помилка VPN-4031, загублений чек |
| недоступні уривки | 2: обидва запитання про щорічну відпустку називають розділ про відпустку керівництва |

Створіть `datasets/bilingual-seed/cases.json`:

```json
{
  "name": "bilingual-seed",
  "version": 1,
  "cases": [
    {
      "id": "en-en-annual-leave-days",
      "question": "How many days of annual leave does an employee get per year?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/annual-leave.md", "heading": "Annual leave policy > Days per year"}
      ],
      "ineligible": [
        {"document": "restricted/executive-leave.md", "heading": "Executive annual leave > Executive days per year"}
      ]
    },
    {
      "id": "en-en-expense-deadline",
      "question": "What is the deadline for submitting an expense report?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/expense-reports.md", "heading": "Expense reports > Submission deadline"}
      ]
    },
    {
      "id": "en-en-vpn-first-connection",
      "question": "How do I connect to the VPN for the first time?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Connecting for the first time"}
      ]
    },
    {
      "id": "en-en-error-exp-2041",
      "question": "What does error EXP-2041 mean?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/expense-reports.md", "heading": "Expense reports > Error code EXP-2041"}
      ]
    },
    {
      "id": "en-en-error-vpn-4031",
      "question": "VPN-4031",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Error VPN-4031"}
      ]
    },
    {
      "id": "en-en-renewed-certificate",
      "question": "How is an expired device certificate renewed?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "morphology",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Error VPN-4031"}
      ]
    },
    {
      "id": "uk-uk-sick-first-day",
      "question": "Що робити в перший день хвороби?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/likarnianyi.md", "heading": "Лікарняний > Перший день хвороби"}
      ]
    },
    {
      "id": "uk-uk-per-diem-abroad",
      "question": "Які добові за кордоном?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/vidriadzhennia.md", "heading": "Службові відрядження > Добові"}
      ]
    },
    {
      "id": "uk-uk-form-it-0457",
      "question": "Як заповнити форму ІТ-0457?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/obladnannia.md", "heading": "Робоче обладнання > Замовлення ноутбука"}
      ]
    },
    {
      "id": "uk-uk-who-pays-sick-leaves",
      "question": "Хто оплачує лікарняні?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "morphology",
      "relevant": [
        {"document": "public/likarnianyi.md", "heading": "Лікарняний > Оплата лікарняного"}
      ]
    },
    {
      "id": "uk-uk-order-laptops",
      "question": "Як замовити ноутбуки?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "morphology",
      "relevant": [
        {"document": "public/obladnannia.md", "heading": "Робоче обладнання > Замовлення ноутбука"}
      ]
    },
    {
      "id": "en-uk-per-diem-abroad",
      "question": "How much is the daily allowance for business trips abroad?",
      "question_language": "en",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/vidriadzhennia.md", "heading": "Службові відрядження > Добові"}
      ]
    },
    {
      "id": "en-uk-who-pays-sick-leave",
      "question": "Who pays for sick leave?",
      "question_language": "en",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/likarnianyi.md", "heading": "Лікарняний > Оплата лікарняного"}
      ]
    },
    {
      "id": "uk-en-annual-leave-days",
      "question": "Скільки днів щорічної відпустки має працівник?",
      "question_language": "uk",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/annual-leave.md", "heading": "Annual leave policy > Days per year"}
      ],
      "ineligible": [
        {"document": "restricted/executive-leave.md", "heading": "Executive annual leave > Executive days per year"}
      ]
    },
    {
      "id": "uk-en-error-vpn-4031",
      "question": "Що означає помилка VPN-4031?",
      "question_language": "uk",
      "evidence_language": "en",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Error VPN-4031"}
      ]
    },
    {
      "id": "uk-en-lost-receipt",
      "question": "Що робити, якщо загубився чек?",
      "question_language": "uk",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/expense-reports.md", "heading": "Expense reports > Error code EXP-2041"}
      ]
    }
  ]
}
```

Запитання підпорядковані двом правилам. Перше: пишіть кожне так, як спитав би працівник, а не копіюючи слова з уривка, бо набір, що копіює текст, нічого не вимірює. Друге: ніколи не змінюйте запитання після того, як побачили його місце. Якщо результат вас здивував, напишіть новий випадок.

Перевірка: `python3 -m json.tool datasets/bilingual-seed/cases.json > /dev/null` нічого не друкує, тобто JSON правильно сформований. Повна перевірка буде на кроці 5.

### Крок 4 — Програмування: напишіть модуль набору

Цей крок пише код, який читає, перевіряє і проганяє набір. Кроки 5–7 спираються на нього.

**Простими словами:** цей файл — і вартовий набору, і той, хто його проганяє. Він читає файл випадків і відхиляє файл із помилкою, пояснюючи, який випадок і яке правило. Потім читає корпус так само, як справжнє завантаження, і переконується, що кожен названий уривок існує рівно один раз. Нарешті, маючи порожню базу даних, він зберігає корпус, ставить кожне запитання від імені одного звичайного працівника і записує, на якому місці опинився кожен правильний уривок. Якщо колись повернеться приватний уривок, він одразу зупиняється.

Що містить модуль:

| Частина | Що робить |
|---|---|
| `Folder`, `QueryClass`, `DIRECTIONS` | Фіксовані позначки: `public` або `restricted`, три види запитань і чотири мовні напрями. |
| `ChunkReference`, `EvaluationCase`, `EvaluationDataset` | Прості значення для одного названого уривка, одного випадку і всього набору. Випадок не показує запитання у своєму repr. |
| `CaseOutcome`, `DatasetReport` | Результат одного випадку (місце кожного релевантного уривка або None) і всього прогону. |
| `DatasetError` і чотири підкласи | `InvalidDataset` (файл або корпус порушує правило), `DatasetRunRefused` (неправильне налаштування або непорожня база), `IneligibleChunkRetrieved` і `CaseUnanswered` (жоден пошук не відповів на випадок). |
| `load_dataset` | Читає файл і перевіряє його форму, а потім правила, яких схема виразити не може. |
| `verify_corpus` | Ріже корпус функціями ingestion і перевіряє кожне посилання. |
| `run_dataset` | Перевіряє свої налаштування, звіряє корпус, завантажує його, проганяє кожен випадок і повертає звіт. |
| `render_report` | Пише звіт у Markdown: кількості за зрізами, потім місця для кожного випадку. |

Створіть `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
"""The bilingual seed dataset: a small corpus and the cases that probe it.

The dataset lives in one folder. `cases.json` holds the Evaluation Cases,
and `corpus/` holds the Documents they search: `corpus/public/` is readable
by everyone, `corpus/restricted/` by nobody. Each case names the Chunks that
answer its Question by Document path and heading path, such as
`public/annual-leave.md` and `Annual leave policy > Days per year`, because
Chunk IDs are new on every ingestion.

What a caller can rely on:

- **Checked on load.** `load_dataset` raises `InvalidDataset` for a file
  that is not valid JSON, has an unknown or missing field, a duplicate case,
  relevant evidence outside `public/`, ineligible evidence outside
  `restricted/`, too few cases for a slice, or a key written twice. Its
  messages never repeat the file's values.
- **Checked against the corpus.** `verify_corpus` reads and chunks the
  corpus the way ingestion does, offline, and raises `InvalidDataset` unless
  every named Chunk exists exactly once and every relevant Document is in
  the case's evidence language.
- **Run as one reader.** `run_dataset` checks its settings, then ingests
  the corpus into an empty database, asks every Question as one ordinary
  employee, and records the rank of each relevant Chunk, or None when it is
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, UserId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import (
    NormalizedDocument,
    Quarantined,
    normalize,
)
from knowledge_service.ingestion.sources import SourceAccess, SourceError
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.language import Language
from knowledge_service.persistence.schema import sources
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrievalUnavailable,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

DATASET_DIRECTORY = Path("datasets/bilingual-seed")
CASES_FILE = "cases.json"
CORPUS_DIRECTORY = "corpus"
MAX_FILE_BYTES = 1_000_000
MAX_NAME_CHARACTERS = 64
MIN_CASES_PER_SLICE = 2
DEFAULT_LIMIT = 10
CASE_TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100

MAX_PATH_CHARACTERS = 200

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Lowercase path segments, so a path is safe to print in an error message.
_PATH = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")


class Folder(StrEnum):
    """Where a corpus Document lives, and so who may read it."""

    PUBLIC = "public"
    RESTRICTED = "restricted"


class QueryClass(StrEnum):
    """What kind of Question a case asks."""

    PLAIN = "plain"
    IDENTIFIER = "identifier"
    MORPHOLOGY = "morphology"


_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
    Folder.PUBLIC: SourceId(UUID(int=0x5EED_0001)),
    Folder.RESTRICTED: SourceId(UUID(int=0x5EED_0002)),
}
READER = AuthorizationContext(user_id=UserId(UUID(int=0x5EED_0003)))
_REQUEST_IDS = UUID(int=0x5EED_0004)


class DatasetError(Exception):
    """The dataset could not be loaded, verified, or run."""


class InvalidDataset(DatasetError):
    """The cases file or the corpus breaks a rule of the dataset."""


class DatasetRunRefused(DatasetError):
    """The run cannot start: the database is not empty or a setting is wrong."""


class IneligibleChunkRetrieved(DatasetError):
    """Retrieval returned a Chunk the reader may not see."""


class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""


@dataclass(frozen=True, slots=True)
class ChunkReference:
    """One Chunk, named by its Document's path and its heading path."""

    folder: Folder
    external_id: str
    heading: str

    @property
    def document(self) -> str:
        """The Document's path inside the corpus, such as `public/a.md`."""
        return f"{self.folder}/{self.external_id}"


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One Question, the Chunks that answer it, and the Chunks it must not see."""

    case_id: str
    question: str = field(repr=False)
    question_language: Language
    evidence_language: Language
    query_class: QueryClass
    relevant: tuple[ChunkReference, ...]
    ineligible: tuple[ChunkReference, ...]

    @property
    def direction(self) -> str:
        """The language asked in and the language answered in, as `uk->en`."""
        return f"{self.question_language}->{self.evidence_language}"


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    """A named, versioned list of verified cases."""

    name: str
    version: int
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Where each relevant Chunk of one case ranked, or None if it was missed."""

    case_id: str
    direction: str
    query_class: QueryClass
    ranks: tuple[int | None, ...]
    degraded: bool

    @property
    def found(self) -> bool:
        """True when at least one relevant Chunk was returned."""
        return any(rank is not None for rank in self.ranks)


@dataclass(frozen=True, slots=True)
class DatasetReport:
    """The outcome of every case of one run, in the dataset's order."""

    name: str
    version: int
    limit: int
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ReferenceFile(_File):
    document: str
    heading: str


class _CaseFile(_File):
    id: str
    question: str
    question_language: Literal["en", "uk"]
    evidence_language: Literal["en", "uk"]
    query_class: Literal["plain", "identifier", "morphology"]
    relevant: list[_ReferenceFile] = Field(min_length=1)
    ineligible: list[_ReferenceFile] = Field(default_factory=list[_ReferenceFile])


class _DatasetFile(_File):
    name: str
    version: int = Field(ge=1)
    cases: list[_CaseFile] = Field(min_length=1)


def load_dataset(path: Path) -> EvaluationDataset:
    """Read and check a cases file; raise `InvalidDataset` if it breaks a rule."""
    # A pipe or a device would block the read, or never end.
    if not path.is_file():
        raise InvalidDataset("the cases file is missing or not a regular file")
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise InvalidDataset(
            f"the cases file cannot be read: {type(error).__name__}"
        ) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidDataset(f"the cases file is larger than {MAX_FILE_BYTES} bytes")
    try:
        content = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except ValueError, RecursionError:
        # UnicodeDecodeError and JSONDecodeError are both ValueErrors.
        raise InvalidDataset("the cases file is not valid UTF-8 JSON") from None
    try:
        parsed = _DatasetFile.model_validate(content)
    except ValidationError as error:
        raise InvalidDataset(_describe(error)) from None
    return _dataset(parsed)


async def verify_corpus(dataset: EvaluationDataset, corpus: Path) -> None:
    """Check every named Chunk against the corpus, chunked as ingestion does.

    Raises `InvalidDataset` when a Document is missing or cannot be ingested,
    when a reference matches no Chunk or more than one, or when a relevant
    Document is not in the case's evidence language.
    """
    documents: dict[tuple[Folder, str], tuple[Language, Counter[str | None]]] = {}
    for folder in Folder:
        for document in await _read_folder(corpus / folder, folder):
            headings = Counter(
                chunk.heading_path for chunk in chunk_by_structure(document.parsed)
            )
            key = (folder, document.provenance.external_id)
            documents[key] = (document.language.language, headings)
    for case in dataset.cases:
        for reference in (*case.relevant, *case.ineligible):
            found = documents.get((reference.folder, reference.external_id))
            if found is None:
                raise InvalidDataset(
                    f"case {case.case_id}: {reference.document} is not in the corpus"
                )
            matches = found[1][reference.heading]
            if matches != 1:
                raise InvalidDataset(
                    f"case {case.case_id}: {matches} Chunks of {reference.document}"
                    " have the named heading path; exactly 1 is required"
                )
        for reference in case.relevant:
            language = documents[(reference.folder, reference.external_id)][0]
            if language is not case.evidence_language:
                raise InvalidDataset(
                    f"case {case.case_id}: {reference.document} is detected as"
                    f" {language}, not {case.evidence_language}"
                )


async def run_dataset(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    limit: int = DEFAULT_LIMIT,
    branches: Mapping[RetrievalBranch, BranchSearch] | None = None,
) -> DatasetReport:
    """Ingest the corpus into an empty database and run every case.

    `branches` defaults to lexical search alone, which needs no provider.
    Raises `DatasetRunRefused` before writing anything when the database
    already holds a Source, `limit` is out of range, or `branches` is not a
    mapping of `RetrievalBranch` to a search; `IneligibleChunkRetrieved`
    when a case returns a restricted Chunk; `CaseUnanswered` when no branch
    answers a case; and `InvalidDataset` like `verify_corpus`. A run that
    fails after ingesting leaves the database used: start the next one on a
    fresh database.
    """
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    searches = (
        {RetrievalBranch.LEXICAL: lexical_branch(runtime)}
        if branches is None
        else branches
    )
    _check_branches(searches)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at)
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
    )


def render_report(report: DatasetReport) -> str:
    """Return the report as Markdown: found counts per slice, then every case."""
    branches = ", ".join(report.branches)
    lines = [
        f"# Retrieval dataset report: {report.name} v{report.version}",
        "",
        f"Branches: {branches}. Each case keeps its top {report.limit} Candidates;"
        " a rank is where a relevant Chunk appears among them.",
        "",
        "## By slice",
        "",
        "| Slice | Cases | Found |",
        "|---|---:|---:|",
    ]
    slices: list[tuple[str, list[CaseOutcome]]] = [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    slices += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
        for query_class in QueryClass
    ]
    for name, outcomes in slices:
        found = sum(outcome.found for outcome in outcomes)
        lines.append(f"| {name} | {len(outcomes)} | {found} |")
    lines += [
        "",
        "## By case",
        "",
        "| Case | Direction | Class | Ranks | Degraded |",
        "|---|---|---|---|---|",
    ]
    for outcome in report.outcomes:
        ranks = ", ".join(
            "missed" if rank is None else str(rank) for rank in outcome.ranks
        )
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {ranks} | {degraded} |"
        )
    return "\n".join(lines) + "\n"


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        # Otherwise the last copy wins, and a reviewer reads the other one.
        raise ValueError("a key appears twice in one object")
    return dict(pairs)


def _no_constant(name: str) -> None:
    raise ValueError("NaN and Infinity are not JSON")


_FIELDS = frozenset(
    name
    for model in (_DatasetFile, _CaseFile, _ReferenceFile)
    for name in model.model_fields
)


def _describe(error: ValidationError) -> str:
    # The message names the place and the rule, never a value. An unknown
    # key is a value too, so only known field names and indexes are shown.
    first = error.errors(include_input=False, include_url=False)[0]
    parts = [
        str(part) if isinstance(part, int) or part in _FIELDS else "<unknown>"
        for part in first["loc"]
    ]
    return f"{'.'.join(parts) or 'file'}: {first['msg']}"


def _dataset(parsed: _DatasetFile) -> EvaluationDataset:
    if not _is_name(parsed.name):
        raise InvalidDataset("name must be lowercase words joined by hyphens")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for index, item in enumerate(parsed.cases):
        if not _is_name(item.id):
            raise InvalidDataset(
                f"cases.{index}.id must be lowercase words joined by hyphens"
            )
        if item.id in seen:
            raise InvalidDataset(f"case {item.id} appears more than once")
        seen.add(item.id)
        cases.append(_case(item))
    _check_coverage(cases)
    return EvaluationDataset(
        name=parsed.name, version=parsed.version, cases=tuple(cases)
    )


def _case(item: _CaseFile) -> EvaluationCase:
    question = item.question
    # Word search refuses a Question with no letter or digit, such as one
    # made of dashes, and that refusal would fail the whole run.
    if not any(c.isalnum() for c in question) or len(question) > MAX_QUERY_CHARACTERS:
        raise InvalidDataset(
            f"case {item.id}: the question must be 1 to"
            f" {MAX_QUERY_CHARACTERS} characters with a letter or digit"
        )
    # PostgreSQL text cannot hold NUL. The JSON parser already refuses a
    # lone surrogate, which UTF-8 cannot hold.
    if "\x00" in question:
        raise InvalidDataset(f"case {item.id}: the question must be valid text")
    relevant = tuple(_reference(item.id, ref) for ref in item.relevant)
    ineligible = tuple(_reference(item.id, ref) for ref in item.ineligible)
    if any(ref.folder is not Folder.PUBLIC for ref in relevant):
        raise InvalidDataset(f"case {item.id}: relevant evidence must be in public/")
    if any(ref.folder is not Folder.RESTRICTED for ref in ineligible):
        raise InvalidDataset(
            f"case {item.id}: ineligible evidence must be in restricted/"
        )
    if len(set(relevant + ineligible)) != len(relevant) + len(ineligible):
        raise InvalidDataset(f"case {item.id} names the same Chunk twice")
    return EvaluationCase(
        case_id=item.id,
        question=question,
        question_language=Language(item.question_language),
        evidence_language=Language(item.evidence_language),
        query_class=QueryClass(item.query_class),
        relevant=relevant,
        ineligible=ineligible,
    )


def _reference(case_id: str, item: _ReferenceFile) -> ChunkReference:
    folder, _, external_id = item.document.partition("/")
    if (
        folder not in set(Folder)
        or len(item.document) > MAX_PATH_CHARACTERS
        or _PATH.fullmatch(external_id) is None
    ):
        raise InvalidDataset(
            f"case {case_id}: a document must be public/<path> or"
            " restricted/<path>, in lowercase letters, digits, and . _ -"
        )
    if not item.heading.strip():
        raise InvalidDataset(f"case {case_id}: a heading must not be blank")
    return ChunkReference(Folder(folder), external_id, item.heading)


def _check_coverage(cases: list[EvaluationCase]) -> None:
    counts = Counter(case.direction for case in cases)
    counts.update(str(case.query_class) for case in cases)
    required = [*DIRECTIONS, QueryClass.IDENTIFIER, QueryClass.MORPHOLOGY]
    for name in required:
        if counts[str(name)] < MIN_CASES_PER_SLICE:
            raise InvalidDataset(
                f"slice {name} needs at least {MIN_CASES_PER_SLICE} cases"
            )
    if not any(case.ineligible for case in cases):
        raise InvalidDataset("at least one case must name ineligible evidence")


def _check_branches(searches: Mapping[RetrievalBranch, BranchSearch]) -> None:
    # Checked here, not only in `retrieve_hybrid`, so a wrong setting is
    # refused before the corpus is written.
    if not isinstance(cast(object, searches), Mapping) or not searches:
        raise DatasetRunRefused("at least one branch is required")
    for branch, search in searches.items():
        if type(branch) is not RetrievalBranch or not callable(search):
            raise DatasetRunRefused(
                "every branch must map a RetrievalBranch to a search"
            )


def _is_name(value: str) -> bool:
    return len(value) <= MAX_NAME_CHARACTERS and _NAME.fullmatch(value) is not None


async def _read_folder(root: Path, folder: Folder) -> list[NormalizedDocument]:
    source = DirectorySource(root, access=SourceAccess(public=True))
    documents: list[NormalizedDocument] = []
    checkpoint: str | None = None
    while True:
        try:
            page = await source.fetch_page(checkpoint=checkpoint, page_size=PAGE_SIZE)
        except SourceError:
            raise InvalidDataset(
                f"the corpus folder {folder}/ cannot be read"
            ) from None
        for record in page.records:
            outcome = normalize(record, source_id=SOURCE_IDS[folder])
            if isinstance(outcome, Quarantined):
                raise InvalidDataset(
                    f"{folder}/{record.external_id} cannot be ingested:"
                    f" {outcome.reason}"
                )
            documents.append(outcome)
        if page.next_checkpoint is None:
            return documents
        checkpoint = page.next_checkpoint


async def _ingest(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    observed_at: datetime,
) -> None:
    async with runtime.transaction() as connection:
        # Held until commit, so a second run started at the same moment
        # waits here, then finds these Sources and is refused.
        await connection.execute(
            sa.text("LOCK TABLE sources IN SHARE ROW EXCLUSIVE MODE")
        )
        if await connection.scalar(sa.select(sa.exists().select_from(sources))):
            raise DatasetRunRefused("the dataset runs only on an empty database")
        for folder, source_id in SOURCE_IDS.items():
            await connection.execute(
                sa.insert(sources).values(
                    source_id=source_id.value,
                    kind="local_directory",
                    location=f"/datasets/{dataset.name}/{folder}",
                    display_name=f"{dataset.name} {folder}",
                    enabled=True,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
            )
    for folder, source_id in SOURCE_IDS.items():
        # Only public/ is readable; restricted/ is granted to nobody.
        access = SourceAccess(public=folder is Folder.PUBLIC)
        summary = await synchronize_source(
            runtime,
            DirectorySource(corpus / folder, access=access),
            source_id=source_id,
            observed_at=observed_at,
        )
        if summary.quarantined:
            raise InvalidDataset(f"a Document in {folder}/ could not be ingested")


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    try:
        result = await retrieve_hybrid(
            case.question,
            context=READER,
            branches=searches,
            limit=limit,
            timeout_seconds=CASE_TIMEOUT_SECONDS,
            request_id=uuid5(_REQUEST_IDS, case.case_id),
            language=case.question_language,
        )
    except HybridRetrievalUnavailable as error:
        failures = ", ".join(
            f"{b.branch} {b.error or b.status}" for b in error.branches
        )
        raise CaseUnanswered(
            f"case {case.case_id}: no branch answered ({failures})"
        ) from None
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in result.candidates:
        if candidate.source_id != SOURCE_IDS[Folder.PUBLIC]:
            raise IneligibleChunkRetrieved(
                f"case {case.case_id} returned a Chunk the reader may not see"
            )
        ranks.setdefault(
            (candidate.external_id, candidate.heading_path), candidate.rank
        )
    return CaseOutcome(
        case_id=case.case_id,
        direction=case.direction,
        query_class=case.query_class,
        ranks=tuple(ranks.get((ref.external_id, ref.heading)) for ref in case.relevant),
        degraded=result.degraded,
    )
```

Як працюють важливі частини:

- **Два шари перевірок у `load_dataset`.** Моделі pydantic (`_DatasetFile`, `_CaseFile`, `_ReferenceFile`) перевіряють форму: кожне поле на місці, немає невідомих полів, типи правильні (`strict=True` не приймає `true` як версію). Потім `_dataset` і `_case` перевіряють те, чого схема не може: унікальні ID, запитання з хоча б однією літерою або цифрою, `public/` для релевантних уривків і `restricted/` для недоступних, достатньо випадків у кожному зрізі.
- **Чому файл спершу розбирає `json.loads`.** Стандартний JSON дозволяє ключу трапитися двічі, і остання копія тихо перемагає. Тоді рецензент, читаючи файл, бачив би одне запитання, а виконувалося б інше. `object_pairs_hook` це відхиляє, а `parse_constant` відхиляє `NaN`, якого в справжньому JSON немає.
- **Тихі помилки.** Помилка називає місце і правило, але ніколи значення. `_describe` відкидає копію вхідних даних, яку робить pydantic. Він також ховає невідомий ключ, бо ключ — це теж текст із файлу. Шляхи документів мають складатися з малих літер, цифр і `. _ -`, тож шлях у повідомленні не може нести перенесення рядка чи керівний код термінала.
- **Чому запитанню потрібна літера або цифра.** Пошук за словами відхиляє запитання лише із символів, наприклад сорок дефісів. Коли пошук за словами — єдина гілка, ніхто б не відповів, і прогін упав би вже після запису корпусу. Відхилення такого запитання під час завантаження зупиняє це заздалегідь.
- **`verify_corpus` перевикористовує ingestion.** Вона читає кожну теку через `DirectorySource`, розбирає через `normalize` і ріже через `chunk_by_structure` — ті самі виклики, що робить `build_chunks`. Вона рахує, скільки фрагментів має кожен шлях заголовків, тож «рівно один» ловить і відсутній розділ, і розділ, що розрізався чи злився.
- **Спершу налаштування, запис наостанок.** `run_dataset` перевіряє `limit` і `branches`, ще не торкаючись бази даних. Потім `_ingest` блокує таблицю `sources`, перевіряє, що вона порожня, і вставляє два джерела в одній транзакції. Блокування змушує другий прогін, запущений у ту саму мить, зачекати, знайти джерела і отримати чисту відмову.
- **Закрите означає «ніхто».** `_ingest` зберігає `restricted/` із `SourceAccess(public=False)`, що не надає доступу нікому. Перевірка дозволів у SQL кожного пошуку потім ховає його від `READER`. `_run_case` перевіряє ще раз на виході: будь-який кандидат не з публічного джерела зупиняє прогін.
- **Фіксовані ID.** ID джерел, читач і ID кожного запиту (`uuid5` від ID випадку) фіксовані. Тому два прогони того самого набору зберігають і питають те саме.

Перевірка: `uv run pyright src/knowledge_service/retrieval/evaluation_dataset.py` показує 0 помилок, а `uv run ruff check src/knowledge_service/retrieval/evaluation_dataset.py` не знаходить проблем.

### Крок 5 — Програмування: напишіть скрипт

Цей крок додає команду, яку ви справді запускатимете. Вона має два режими: швидку перевірку, якій потрібні лише файли, і повний прогін на базі даних.

**Простими словами:** скрипт завантажує випадки, а потім або звіряє їх із корпусом і каже, скільки пройшло, або проганяє їх на порожній базі даних і записує звіт у файл. Будь-яка проблема з набором завершує скрипт однорядковою причиною замість довгого трасування помилки.

- `--verify-only` запускає `load_dataset` і `verify_corpus`; база даних не потрібна.
- Без нього скрипт читає `KNOWLEDGE_SERVICE_DATABASE_URL`, запускає `run_dataset`, пише `artifacts/evaluations/bilingual-seed.md` (Git ігнорує `artifacts/`) і друкує звіт.
- `--limit` змінює, скільки кандидатів зберігає кожен випадок; `--dataset` вказує на іншу теку набору.
- Код виходу 0 при успіху, 1 для будь-якої `DatasetError`.

Створіть `scripts/run_retrieval_dataset.py`:

```python
"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. The report goes to
`artifacts/evaluations/bilingual-seed.md`, which Git ignores, and is printed.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    DatasetReport,
    EvaluationDataset,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=REPORT)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        runtime = create_database_runtime(Settings())
        report = asyncio.run(_run(runtime, dataset, corpus, arguments.limit))
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    rendered = render_report(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {arguments.output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> DatasetReport:
    try:
        return await run_dataset(
            runtime, dataset, corpus, observed_at=datetime.now(UTC), limit=limit
        )
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
```

Перевірка:

```bash
uv run python scripts/run_retrieval_dataset.py --verify-only
```

Очікувано: `16 cases verified`. Тепер навмисно зламайте один заголовок: змініть `"Annual leave policy > Days per year"` у першому випадку на `"Annual leave policy > Days"` і запустіть знову. Очікувано: `dataset run failed: case en-en-annual-leave-days: 0 Chunks of public/annual-leave.md have the named heading path; exactly 1 is required`. Скасуйте зміну.

### Крок 6 — Програмування: протестуйте правила файлу і перевірку корпусу

Цей крок без бази даних доводить, що справжній набір проходить, а кожен вид помилки відхиляється з правильним повідомленням. Ці тести запускаються на кожному коміті.

**Простими словами:** тести беруть справжній файл випадків, змінюють по одній речі й перевіряють, що завантажувач відхиляє результат і пояснює чому. Ще вони згодовують йому випадкове сміття, щоб переконатися, що він ніколи не падає несподіваним чином і не повторює того, що отримав. Потім вони псують копію корпусу і перевіряють, що перевірка корпусу це помічає.

| Тест | Що доводить |
|---|---|
| `test_the_seed_dataset_loads_and_matches_its_corpus` | Успіх: справжній набір завантажується, проходить перевірку і покриває кожен зріз. |
| `test_a_cases_file_that_breaks_a_rule_is_refused` | 29 окремих помилок, кожна відхиляється зі своїм повідомленням. |
| `test_a_file_that_is_not_a_dataset_is_refused`, `test_json_that_is_ambiguous_or_broken_is_refused` | Порожні, зламані файли, файли з повтореним ключем, `NaN`, дуже глибокою вкладеністю, величезним числом або не в UTF-8 відхиляються. |
| `test_a_missing_cases_file_is_refused`, `test_an_unreadable_cases_file_is_refused`, `test_a_pipe_in_place_of_the_cases_file_is_refused_without_waiting` | Відсутній, недоступний для читання файл або канал (pipe) відхиляються, і канал не змушує завантажувач чекати вічно. |
| Два тести `..._raises_only_invalid_dataset_without_echoing_it` | Hypothesis закладає маркер у випадкові значення; єдина помилка — `InvalidDataset`, і маркер у ній ніколи не з'являється. |
| `test_an_error_never_repeats_a_key_or_a_path_from_the_file` | Невідомий ключ або шлях із перенесенням рядка не повторюються в повідомленні. |
| `test_a_case_that_does_not_match_the_corpus_is_refused`, `test_a_heading_shared_by_two_chunks_is_refused` | Відсутній документ, неправильний заголовок, неправильна мова або заголовок, що відповідає двом фрагментам, не проходять перевірку. |
| `test_a_corpus_document_that_cannot_be_ingested_is_refused`, `test_a_missing_corpus_folder_is_refused`, `test_the_corpus_is_read_page_by_page` | Проблеми корпусу типізовані, а читання працює посторінково. |
| `test_a_wrong_setting_is_refused_before_any_work` | Неправильний `limit` або `branches` кидає `DatasetRunRefused` ще до будь-якого з'єднання. |
| `test_the_report_counts_slices_and_shows_ranks_only` | Кількості за зрізами і стовпець місць правильні. |

Створіть `tests/test_evaluation_dataset.py`:

```python
"""The bilingual seed dataset: its file rules, its corpus check, its report."""

import json
import os
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from knowledge_service.database import create_database_runtime
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult, BranchSearch
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES
from knowledge_service.settings import Settings

MARKER = "PLANTED-MARKER-4417"

type Content = dict[str, Any]
type Edit = Callable[[Content], None]


def seed() -> Content:
    return json.loads((DATASET_DIRECTORY / CASES_FILE).read_text(encoding="utf-8"))


def copy_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "dataset"
    shutil.copytree(DATASET_DIRECTORY, target)
    return target


def write_cases(folder: Path, content: Content) -> Path:
    path = folder / CASES_FILE
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def edited(edit: Edit) -> Content:
    content = seed()
    edit(content)
    return content


def first_case(content: Content) -> Content:
    return content["cases"][0]


def top(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        content[key] = value

    return edit


def case_field(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        first_case(content)[key] = value

    return edit


def without_case_field(key: str) -> Edit:
    def edit(content: Content) -> None:
        del first_case(content)[key]

    return edit


def reference(key: str, value: str) -> Edit:
    def edit(content: Content) -> None:
        first_case(content)["relevant"][0][key] = value

    return edit


def duplicate_first_case(content: Content) -> None:
    content["cases"].append(dict(first_case(content)))


def relevant_from_restricted(content: Content) -> None:
    case = first_case(content)
    case["relevant"].append(case["ineligible"][0])


def ineligible_from_public(content: Content) -> None:
    case = first_case(content)
    case["ineligible"].append(case["relevant"][0])


def relevant_twice(content: Content) -> None:
    case = first_case(content)
    case["relevant"].append(case["relevant"][0])


def drop_class(query_class: str) -> Edit:
    def edit(content: Content) -> None:
        content["cases"] = [
            case for case in content["cases"] if case["query_class"] != query_class
        ]

    return edit


def only_english_evidence(content: Content) -> None:
    content["cases"] = [
        case for case in content["cases"] if case["evidence_language"] == "en"
    ]


def without_ineligible(content: Content) -> None:
    for case in content["cases"]:
        case.pop("ineligible", None)


async def test_the_seed_dataset_loads_and_matches_its_corpus() -> None:
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    await verify_corpus(dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)

    assert (dataset.name, dataset.version) == ("bilingual-seed", 1)
    directions = Counter(case.direction for case in dataset.cases)
    classes = Counter(case.query_class for case in dataset.cases)
    assert all(directions[direction] >= 2 for direction in DIRECTIONS)
    assert classes[QueryClass.IDENTIFIER] >= 2
    assert classes[QueryClass.MORPHOLOGY] >= 2
    assert any(case.ineligible for case in dataset.cases)


def test_a_case_keeps_its_question_out_of_repr() -> None:
    case = load_dataset(DATASET_DIRECTORY / CASES_FILE).cases[0]

    assert case.question not in repr(case)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (case_field("extra", 1), "<unknown>: Extra inputs"),
        (without_case_field("question"), "question"),
        (case_field("question_language", "de"), "question_language"),
        (case_field("query_class", "fuzzy"), "query_class"),
        (case_field("relevant", []), "relevant"),
        (top("version", True), "version"),
        (top("version", 0), "version"),
        (top("cases", []), "cases"),
        (top("name", "Bilingual Seed"), "name"),
        (case_field("id", "Case 1"), "id"),
        (duplicate_first_case, "more than once"),
        (case_field("question", "   "), "letter or digit"),
        (case_field("question", "-" * 40), "letter or digit"),
        (case_field("question", chr(0x200B)), "letter or digit"),
        (case_field("question", "a" * (MAX_QUERY_CHARACTERS + 1)), "characters"),
        (case_field("question", "leave\x00days"), "valid text"),
        (reference("document", "annual-leave.md"), "public/<path>"),
        (reference("document", "private/a.md"), "public/<path>"),
        (reference("document", "public/Annual-Leave.md"), "public/<path>"),
        (reference("document", "public/../restricted/a.md"), "public/<path>"),
        (reference("document", "public/" + "a" * 200 + ".md"), "public/<path>"),
        (reference("heading", " "), "heading"),
        (relevant_from_restricted, "relevant evidence must be in public/"),
        (ineligible_from_public, "ineligible evidence must be in restricted/"),
        (relevant_twice, "same Chunk twice"),
        (drop_class("morphology"), "slice morphology"),
        (drop_class("identifier"), "slice identifier"),
        (only_english_evidence, "slice en->uk"),
        (without_ineligible, "ineligible evidence"),
    ],
)
def test_a_cases_file_that_breaks_a_rule_is_refused(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    path = write_cases(tmp_path, edited(edit))

    with pytest.raises(InvalidDataset, match=message):
        load_dataset(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "file"),
        (b"{not json", "file"),
        (b"[]", "file"),
        (b'{"name": "x", "version": 1, "cases": [', "file"),
        (b" " * (MAX_FILE_BYTES + 1), "larger than"),
    ],
)
def test_a_file_that_is_not_a_dataset_is_refused(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / CASES_FILE
    path.write_bytes(content)

    with pytest.raises(InvalidDataset, match=message):
        load_dataset(path)


def test_a_missing_cases_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataset, match="not a regular file"):
        load_dataset(tmp_path / CASES_FILE)


def test_an_unreadable_cases_file_is_refused(tmp_path: Path) -> None:
    path = write_cases(tmp_path, seed())
    path.chmod(0)
    try:
        with pytest.raises(InvalidDataset, match="cannot be read: PermissionError"):
            load_dataset(path)
    finally:
        path.chmod(0o600)


def test_a_pipe_in_place_of_the_cases_file_is_refused_without_waiting(
    tmp_path: Path,
) -> None:
    os.mkfifo(tmp_path / CASES_FILE)

    with pytest.raises(InvalidDataset, match="not a regular file"):
        load_dataset(tmp_path / CASES_FILE)


@pytest.mark.parametrize(
    "content",
    [
        b'{"name": "a", "name": "b", "version": 1, "cases": []}',
        b'{"name": "bilingual-seed", "version": NaN, "cases": []}',
        b"[" * 100_000,
        b'{"version": 1' + b"0" * 5000 + b"}",
        b"\xff\xfe",
    ],
)
def test_json_that_is_ambiguous_or_broken_is_refused(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / CASES_FILE
    path.write_bytes(content)

    with pytest.raises(InvalidDataset, match="not valid UTF-8 JSON"):
        load_dataset(path)


def test_a_key_written_twice_is_refused_in_the_real_file(tmp_path: Path) -> None:
    raw = (DATASET_DIRECTORY / CASES_FILE).read_text(encoding="utf-8")
    twice = raw.replace('"question": ', f'"question": "{MARKER}", "question": ', 1)
    path = tmp_path / CASES_FILE
    path.write_text(twice, encoding="utf-8")

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "edit",
    [
        case_field(MARKER + "\nforged: line", 1),
        reference("document", f"public/{MARKER}\x1b[2J\nforged: line"),
    ],
)
def test_an_error_never_repeats_a_key_or_a_path_from_the_file(
    tmp_path: Path, edit: Edit
) -> None:
    path = write_cases(tmp_path, edited(edit))

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)
    assert "\n" not in str(raised.value)


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False)
    | st.text(max_size=20).map(lambda text: MARKER + text),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=10,
)
fields = st.sampled_from(
    [
        "id",
        "question",
        "question_language",
        "evidence_language",
        "query_class",
        "relevant",
        "ineligible",
    ]
)


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(name=fields, value=json_values)
def test_any_field_value_raises_only_invalid_dataset_without_echoing_it(
    tmp_path: Path, name: str, value: object
) -> None:
    content = seed()
    first_case(content)[name] = value
    path = write_cases(tmp_path, content)

    # Some values are valid, such as a new question; any error must be typed.
    message = ""
    try:
        load_dataset(path)
    except InvalidDataset as error:
        message = str(error)
    assert MARKER not in message


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=json_values)
def test_any_reference_value_raises_only_invalid_dataset_without_echoing_it(
    tmp_path: Path, value: object
) -> None:
    content = seed()
    first_case(content)["relevant"] = [value]
    path = write_cases(tmp_path, content)

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (reference("document", "public/missing.md"), "not in the corpus"),
        (reference("heading", "Annual leave"), "0 Chunks"),
        (case_field("evidence_language", "uk"), "detected as en, not uk"),
    ],
)
async def test_a_case_that_does_not_match_the_corpus_is_refused(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    folder = copy_dataset(tmp_path)
    dataset = load_dataset(write_cases(folder, edited(edit)))

    with pytest.raises(InvalidDataset, match=message):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_heading_shared_by_two_chunks_is_refused(tmp_path: Path) -> None:
    folder = copy_dataset(tmp_path)
    document = folder / CORPUS_DIRECTORY / "public" / "annual-leave.md"
    text = document.read_text(encoding="utf-8")
    section = text[text.index("## Days per year") :].split("\n\n## ")[0]
    document.write_text(f"{text}\n{section}\n", encoding="utf-8")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match="2 Chunks"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_corpus_document_that_cannot_be_ingested_is_refused(
    tmp_path: Path,
) -> None:
    folder = copy_dataset(tmp_path)
    (folder / CORPUS_DIRECTORY / "restricted" / "empty.md").write_text("")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match=r"restricted/empty.md cannot be"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_missing_corpus_folder_is_refused(tmp_path: Path) -> None:
    folder = copy_dataset(tmp_path)
    shutil.rmtree(folder / CORPUS_DIRECTORY / "restricted")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match="restricted/ cannot be read"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_the_corpus_is_read_page_by_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluation_dataset, "PAGE_SIZE", 1)
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    await verify_corpus(dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)


async def never_called(request: BranchRequest) -> BranchResult:
    raise AssertionError("no branch may run")


@pytest.mark.parametrize(
    ("limit", "branches", "message"),
    [
        (0, None, "limit"),
        (MAX_CANDIDATES + 1, None, "limit"),
        (True, None, "limit"),
        (10, {}, "at least one branch"),
        (10, {"lexical": never_called}, "every branch"),
        (10, {RetrievalBranch.LEXICAL: "search"}, "every branch"),
    ],
)
async def test_a_wrong_setting_is_refused_before_any_work(
    limit: int, branches: Mapping[RetrievalBranch, BranchSearch] | None, message: str
) -> None:
    # The runtime connects lazily, so nothing is reached at this address.
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await run_dataset(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 3, tzinfo=UTC),
            limit=limit,
            branches=branches,
        )
    await runtime.dispose()


def test_the_report_counts_slices_and_shows_ranks_only() -> None:
    report = DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=10,
        branches=(RetrievalBranch.LEXICAL,),
        outcomes=(
            CaseOutcome("a", "en->en", QueryClass.PLAIN, (1,), degraded=False),
            CaseOutcome("b", "en->en", QueryClass.IDENTIFIER, (None,), degraded=False),
            CaseOutcome("c", "uk->en", QueryClass.PLAIN, (None, 3), degraded=True),
        ),
    )

    rendered = render_report(report)

    assert "| en->en | 2 | 1 |" in rendered
    assert "| uk->en | 1 | 1 |" in rendered
    assert "| uk->uk | 0 | 0 |" in rendered
    assert "| plain | 2 | 2 |" in rendered
    assert "| c | uk->en | plain | missed, 3 | yes |" in rendered
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")
```

Тест налаштувань створює середовище бази даних з адресою, де ніхто не слухає. Це безпечно, бо середовище з'єднується лише під час першого використання, а тест очікує відмову ще до того. `never_called` кидає помилку, якщо якась гілка колись запуститься.

Перевірка:

```bash
uv run pytest tests/test_evaluation_dataset.py -q
```

Очікувано: 63 passed.

### Крок 7 — Програмування: протестуйте прогін на справжній базі даних

Цей крок доводить гарантії, які може показати лише справжня база даних: корпус збережено, закрита сторінка лишається прихованою, і нічого приватного не потрапляє у звіт чи логи.

**Простими словами:** тести зберігають корпус у свіжій тестовій базі даних і проганяють кожен випадок. Потім вони перевіряють чотири речі. Відомі добрі збіги стоять першими. Приватна сторінка справді збережена, але пошук її маркера нічого не знаходить. Фальшивий пошук, що повертає приватну сторінку, зупиняє прогін. А другий прогін на тій самій базі відхиляється, нічого не записавши.

| Тест | Що доводить |
|---|---|
| `test_every_case_runs_and_the_report_names_no_text` | Успіх: кожен випадок проганяється по черзі, три відомі збіги стоять першими, і ні закритий маркер, ні жодне запитання не з'являються у звіті чи логах. |
| `test_the_restricted_document_is_stored_but_never_found` | Найризикованіший крайній випадок: обидва закриті фрагменти є в базі даних, але пошук їхнього точного маркера від імені `READER` нічого не повертає. |
| `test_a_branch_that_returns_a_restricted_chunk_stops_the_run` | Типізована відмова: фальшивий пошук, що «протікає», кидає `IneligibleChunkRetrieved`, а повідомлення називає випадок, а не текст. |
| `test_a_database_that_is_not_empty_is_refused_before_any_write` | Другий прогін кидає `DatasetRunRefused` і не додає рядків. |
| `test_a_case_no_branch_answers_raises_a_typed_error` | Пошук, що відхиляє кожен випадок, дає `CaseUnanswered` із типом помилки гілки. |
| `test_two_runs_at_once_give_one_report_and_one_refusal` | Два прогони, запущені разом: один вдається, другий отримує чисту відмову. |

Найважливіший — тест «збережено, але ніколи не знайдено». Без рядка `stored == 2` він пройшов би і тоді, коли закриту сторінку взагалі не завантажили, а це нічого не доводило б про дозволи.

Створіть `tests/test_evaluation_dataset_integration.py`:

```python
"""The bilingual seed dataset on real PostgreSQL: ingested, run, and quiet."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.schema import chunks, documents, sources
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    CaseUnanswered,
    DatasetReport,
    DatasetRunRefused,
    EvaluationDataset,
    Folder,
    IneligibleChunkRetrieved,
    load_dataset,
    render_report,
    run_dataset,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
# Written into every section of the restricted Document.
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def dataset() -> EvaluationDataset:
    return load_dataset(DATASET_DIRECTORY / CASES_FILE)


@dataclass(frozen=True, slots=True)
class FakeCandidate:
    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    external_id: str
    title: str
    heading_path: str | None
    source_anchor: str | None
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class FakeResult:
    candidates: tuple[FakeCandidate, ...]
    truncated: bool = False


@pytest.mark.integration
async def test_every_case_runs_and_the_report_names_no_text(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    report = await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    outcomes = {outcome.case_id: outcome for outcome in report.outcomes}
    assert [o.case_id for o in report.outcomes] == [c.case_id for c in dataset.cases]
    assert report.branches == (RetrievalBranch.LEXICAL,)
    assert not any(outcome.degraded for outcome in report.outcomes)
    # Every word of these Questions is in the relevant section.
    assert outcomes["en-en-expense-deadline"].ranks == (1,)
    assert outcomes["en-en-error-vpn-4031"].ranks == (1,)
    assert outcomes["uk-uk-per-diem-abroad"].ranks == (1,)
    rendered = render_report(report)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_restricted_document_is_stored_but_never_found(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
        )
        found = await search_lexical_chunks(
            connection, text=RESTRICTED_MARKER, context=READER, limit=10
        )
    assert stored == 2
    assert found.candidates == ()


@pytest.mark.integration
async def test_a_branch_that_returns_a_restricted_chunk_stops_the_run(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def leaking(request: BranchRequest) -> BranchResult:
        return FakeResult(
            candidates=(
                FakeCandidate(
                    rank=1,
                    chunk_id=ChunkId.new(),
                    document_id=DocumentId.new(),
                    source_id=SOURCE_IDS[Folder.RESTRICTED],
                    content_version=ContentVersion(1),
                    ordinal=1,
                    external_id="executive-leave.md",
                    title="Executive annual leave",
                    heading_path="Executive annual leave > Executive days per year",
                    source_anchor=None,
                    text=RESTRICTED_MARKER,
                ),
            )
        )

    with pytest.raises(IneligibleChunkRetrieved) as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: leaking},
        )
    assert dataset.cases[0].case_id in str(raised.value)
    assert RESTRICTED_MARKER not in str(raised.value)


@pytest.mark.integration
async def test_a_database_that_is_not_empty_is_refused_before_any_write(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)
    async with runtime.transaction() as connection:
        before = await connection.scalar(sa.select(sa.func.count()).select_from(chunks))

    with pytest.raises(DatasetRunRefused, match="empty database"):
        await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    async with runtime.transaction() as connection:
        after = await connection.scalar(sa.select(sa.func.count()).select_from(chunks))
        source_rows = await connection.scalar(
            sa.select(sa.func.count()).select_from(sources)
        )
    assert after == before
    assert source_rows == 2


@pytest.mark.integration
async def test_a_case_no_branch_answers_raises_a_typed_error(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def refusing(request: BranchRequest) -> BranchResult:
        raise InvalidLexicalQuery("refused")

    with pytest.raises(CaseUnanswered, match="InvalidLexicalQuery") as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: refusing},
        )
    assert dataset.cases[0].case_id in str(raised.value)


@pytest.mark.integration
async def test_two_runs_at_once_give_one_report_and_one_refusal(
    migrated_database: str, dataset: EvaluationDataset
) -> None:
    runtimes = [
        create_database_runtime(Settings(database_url=SecretStr(migrated_database)))
        for _ in range(2)
    ]
    try:
        results = await asyncio.gather(
            *(
                run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)
                for runtime in runtimes
            ),
            return_exceptions=True,
        )
    finally:
        for runtime in runtimes:
            await runtime.dispose()

    kinds = sorted(type(result).__name__ for result in results)
    assert kinds == sorted([DatasetReport.__name__, DatasetRunRefused.__name__])
```

`FakeCandidate` має кожне поле протоколу `Candidate` з M05-T04, тож злиття приймає його як справжнього.

Перевірка:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_evaluation_dataset_integration.py -q
```

Очікувано: 6 passed. Без URL усі шість пропускаються (skipped), а не виконуються.

### Крок 8 — Без програмування: проженіть набір і прочитайте звіт

Цей крок проганяє набір по-справжньому, двічі, і порівнює результат із вашими передбаченнями. Ще він навмисно ламає правило дозволів, щоб побачити, як тести це ловлять.

**Дія 1:** створіть порожню базу даних, застосуйте міграції і запустіть. URL містить пароль, тож він живе лише у змінній оболонки.

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py
```

Еталонний прогін надрукував:

```text
# Retrieval dataset report: bilingual-seed v1

Branches: lexical. Each case keeps its top 10 Candidates; a rank is where a relevant Chunk appears among them.

## By slice

| Slice | Cases | Found |
|---|---:|---:|
| en->en | 6 | 4 |
| en->uk | 2 | 0 |
| uk->en | 3 | 0 |
| uk->uk | 5 | 1 |
| plain | 9 | 3 |
| identifier | 4 | 1 |
| morphology | 3 | 1 |

## By case

| Case | Direction | Class | Ranks | Degraded |
|---|---|---|---|---|
| en-en-annual-leave-days | en->en | plain | missed | no |
| en-en-expense-deadline | en->en | plain | 1 | no |
| en-en-vpn-first-connection | en->en | plain | 1 | no |
| en-en-error-exp-2041 | en->en | identifier | missed | no |
| en-en-error-vpn-4031 | en->en | identifier | 1 | no |
| en-en-renewed-certificate | en->en | morphology | 1 | no |
| uk-uk-sick-first-day | uk->uk | plain | missed | no |
| uk-uk-per-diem-abroad | uk->uk | plain | 1 | no |
| uk-uk-form-it-0457 | uk->uk | identifier | missed | no |
| uk-uk-who-pays-sick-leaves | uk->uk | morphology | missed | no |
| uk-uk-order-laptops | uk->uk | morphology | missed | no |
| en-uk-per-diem-abroad | en->uk | plain | missed | no |
| en-uk-who-pays-sick-leave | en->uk | plain | missed | no |
| uk-en-annual-leave-days | uk->en | plain | missed | no |
| uk-en-error-vpn-4031 | uk->en | identifier | missed | no |
| uk-en-lost-receipt | uk->en | plain | missed | no |

Full report: artifacts/evaluations/bilingual-seed.md
```

**Дія 2:** доведіть, що прогін повторюваний. Запустіть скрипт ще раз на тій самій базі: він має відмовити з `dataset run failed: the dataset runs only on an empty database`. Потім почніть зі свіжої бази і порівняйте:

```bash
cp artifacts/evaluations/bilingual-seed.md /tmp/first-run.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py > /dev/null
diff /tmp/first-run.md artifacts/evaluations/bilingual-seed.md && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Очікувано: `same`.

**Дія 3:** прочитайте звіт поруч зі своїми передбаченнями. Кожен пункт нижче пояснює одну закономірність:

- **Англійським збігам потрібне кожне слово.** "What is the deadline for submitting an expense report?" знаходиться, бо кожне слово, яке не є англійським стоп-словом, є в розділі. "How many days of annual leave does an employee get per year?" промахується, бо "many" і "get" немає в тексті.
- **В українській немає стоп-слів і основ.** «Які добові за кордоном?» знаходиться, бо всі три слова є в розділі точно в такій формі. «Що робити в перший день хвороби?» промахується: «робити» і «в» теж обов'язкові, а в розділі їх немає.
- **Морфологія промахується українською, але не англійською.** «лікарняні» — це не «лікарняного», а `simple` зберігає слова як є. Англійське "renewed" стає "renew", тож цей випадок знаходиться.
- **Кожен міжмовний випадок промахується.** Пошук за словами не може зіставити «відпустки» з "leave". Навіть випадок з ідентифікатором «Що означає помилка VPN-4031?» промахується, бо його українські слова теж мають збігтися.
- **Закрита сторінка ніколи не з'являється,** і прогін не зупинився, тож жоден пошук її не повернув.

Запишіть у файл доказів, які передбачення справдилися, які ні, і чому. Ці числа — точка відліку, з якою порівнюватиме M05-T08.

**Дія 4:** навмисно зламайте правило дозволів. У `_ingest` змініть `access = SourceAccess(public=folder is Folder.PUBLIC)` на `access = SourceAccess(public=True)` і запустіть інтеграційні тести з кроку 7. Очікувано: падає `test_the_restricted_document_is_stored_but_never_found`, бо пошук маркера тепер знаходить закриту сторінку. Запустіть і скрипт на свіжій базі: він усе одно вдається. У кожному запитанні набору є хоча б одне слово, якого немає в закритому розділі, тож пошук за словами ніколи його не повертає, і власна перевірка прогону не має що зупиняти. Ця перевірка стане важливою в M05-T08, коли пошук за змістом зможе повернути уривок без точного збігу слів. Скасуйте зміну і запускайте тести знову, доки вони не пройдуть.

### Крок 9 — Без програмування: перевірки, документація, докази і прогрес

Цей крок доводить, що все досі працює, оновлює єдиний рядок архітектури, який зміна зробила застарілим, і записує результат.

**Дія 1:** у `docs/architecture.md` змініть рядок `retrieval` у таблиці "Code layout" на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_evaluation_dataset.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make check
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав `make test-unit` 1160 passed, `make test-integration` 294 passed і `make check` 1454 passed, 3 deselected.

**Дія 3:** заповніть файл доказів:

- **Design:** структура тек, чому випадки називають уривки за шляхом і заголовком, два шари перевірок, чому перевірка працює без бази, два джерела й один читач, чому прогону потрібна порожня база і чому за замовчуванням працює лише пошук за словами.
- **Verification:** команди й кількості, звіт із кроку 8, `diff` повторного прогону, навмисна поломка з кроку 8 і відомі обмеження з розділу «Перш ніж почати».
- **Review:** змагальне рев'ю (adversarial review) прототипу знайшло шість проблем, усі виправлено з регресійними тестами. Запитання лише із символів проходило перевірки, а потім валило прогін уже після запису корпусу; тепер його відхиляють під час завантаження. Неправильні налаштування `branches` ловилися лише після запису; тепер їх перевіряють першими. Два одночасні прогони давали сиру помилку бази даних; тепер блокування таблиці перетворює другий на чисту відмову. Помилки повторювали невідомі ключі та шляхи документів із файлу; тепер не показують ні того, ні іншого. Двічі записаний ключ тихо приймався; тепер його відхиляють. Канал (pipe) на місці файлу випадків змушував завантажувач чекати вічно; тепер його відхиляють.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T07 до `docs/curriculum/PROGRESS.md`, укажіть наступним завданням M05-T08 і зробіть коміт із повідомленням `m05-t07: create the first bilingual retrieval set`.

## Запитання для роздумів

1. Що `run_dataset` ховає від коду, який його викликає? Перелічіть, про що викликачеві ніколи не треба думати: наприклад, рядки джерел, дозволи чи те, як випадок називає свій уривок.
2. Згодом хтось пришвидшує прогін, пропускаючи `verify_corpus`, бо «корпус не змінювався». Яке неправильне число тоді може показати звіт, і як хтось це помітить?
3. Хтось інший редагує запитання, побачивши, що воно промахується, щоб тепер воно збігалося. Чому це робить набір гіршим для вимірювання наступного способу пошуку, хоча звіт виглядає краще?
4. Кожен міжмовний випадок промахується з пошуком за словами. Якого результату ви очікуєте від векторного пошуку в M05-T08, і промах якого одного випадку вас здивував би найбільше?
5. Шістнадцять випадків — це мало. Виберіть один зріз і скажіть, який новий випадок міг би довести, що ваше уявлення про нього хибне. Наприклад, українське запитання, у відповіді на яке вжито рідкісну форму слова.

## Контрольний список завершення

- [ ] Файл доказів має інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] Сім файлів корпусу існують, а команда з кроку 2 показує 21 фрагмент без повторених заголовків.
- [ ] `cases.json` має 16 випадків, що покривають усі чотири напрями, ідентифікатори, морфологію і недоступні уривки.
- [ ] `uv run python scripts/run_retrieval_dataset.py --verify-only` друкує `16 cases verified`.
- [ ] Зламаний файл випадків кидає `InvalidDataset` із повідомленням, що називає випадок і правило, а не значення.
- [ ] Закриту сторінку збережено, але ніколи не повернуто, а її маркера немає ні у звіті, ні в логах.
- [ ] Пошук, що «протікає», кидає `IneligibleChunkRetrieved`; непорожня база кидає `DatasetRunRefused` до будь-якого запису.
- [ ] Два прогони на свіжих базах дали однаковий звіт.
- [ ] Ви виконали навмисну поломку з кроку 8, побачили, як падає тест, і скасували її.
- [ ] Ви порівняли звіт зі своїми передбаченнями у файлі доказів.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, модульні та інтеграційні тести, документація.
- [ ] Рядок архітектури, докази, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим списком.
