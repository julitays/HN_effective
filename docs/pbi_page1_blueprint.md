# Power BI: blueprint первой страницы

## 1. Что уже сделано

- нормализация регионов перенесена в ETL
- в parquet уже добавлены `Регион BI` и `Группа региона`
- в parquet уже добавлены ключи периодов:
  - `MonthStart`, `YearMonth`
  - `QuarterStart`, `YearQuarter`, `QuarterLabel`
  - для адаптационных витрин: `QuarterStart ОЭД`, `YearQuarter ОЭД`
- служебные таблицы уже собраны в `data/out`:
  - `dRegion.parquet`
  - `dMonth.parquet`
  - `dQuarter.parquet`
- в месячный снапшот и календарь уже добавлены поля для фильтров:
  - `Название месяца`
  - `Месяц коротко`
  - `Месяц`
  - `Год`
  - `Месяц номер`
- статус и приоритеты уже считаются в ETL по единой управленческой логике:
  - `Высокий риск` — только при сильном индексе риска или нескольких красных сигналах
  - `Контроль` — при одиночном красном или нескольких мягких сигналах
  - `Стабильно` — если критичных сигналов нет
- если у региона нет ни одного сигнала, он остаётся `Стабильно` и в блок `Приоритеты` не попадает

Значит, в Power BI это больше не нужно делать руками.

---

## 2. Что подключать в Power BI

### Размерности

- `dim_employees` → `data/out/dim_employees.parquet`
- `dRegion` → `data/out/dRegion.parquet`
- `dMonth` → `data/out/dMonth.parquet`
- `dQuarter` → `data/out/dQuarter.parquet`

### Факты

- `kpi_fact` → `data/out/kpi_fact.parquet`
- `okk_fact` → `data/out/okk_fact.parquet`
- `fact_oed` → `data/out/fact_oed.parquet`
- `learning_fact` → `data/out/learning_fact.parquet`
- `learning_monthly` → `data/out/learning_monthly.parquet`
- `enps_fact` → `data/out/enps_fact.parquet`

---

## 3. Проверенные названия колонок

Ниже только те поля, которые реально проверены в parquet и нужны для первой страницы.

### `dim_employees`

- `ID сотрудника` — `text`
- `Активен` — `boolean`
- `Регион BI` — `text`
- `Группа региона`
- `ФИО`
- `Должность`
- `Город`

### `kpi_fact`

- `ID мерчендайзера` — `text`
- `KPI 1` — `decimal`
- `KPI 2` — `decimal`
- `Вакансия` — `boolean`
- `Регион BI` — `text`
- `MonthStart` — `date/datetime`
- `YearMonth` — `whole number`

### `okk_fact`

- `ID мерчендайзера` — `text`
- `Качество визита` — `decimal`
- `Флаг фальсификации` — `boolean`
- `Регион BI` — `text`
- `MonthStart` — `date/datetime`
- `YearMonth` — `whole number`

### `fact_oed`

- `ID сотрудника` — `text`
- `Аттестация` — `decimal`
- `Рейтинг` — `decimal`
- `Риск оттока` — `boolean`
- `Регион BI` — `text`
- `QuarterStart` — `date/datetime`
- `YearQuarter` — `whole number`
- `QuarterLabel` — `text`

### `learning_fact`

- `ID сотрудника` — `text`
- `Обязательный` — `boolean`
- `Пройдено` — `boolean`
- `Регион BI` — `text`
- `MonthStart` — `date/datetime`
- `YearMonth` — `whole number`
- `StartMonth` — `date/datetime`
- `StartYearMonth` — `whole number`

### `learning_monthly`

- `MonthStart` — `date/datetime`
- `YearMonth` — `whole number`
- `Регион BI` — `text`
- `Назначено обязательных курсов` — `whole number`
- `Пройдено обязательных курсов` — `whole number`
- `Сотрудников с курсами` — `whole number`
- `Обязательное обучение %` — `decimal`

### `enps_fact`

- `Уровень риска ухода` — `text`
- `Составной риск ухода` — `whole number`
- `Регион BI` — `text`
- `QuarterStart` — `date/datetime`
- `YearQuarter` — `whole number`
- `QuarterLabel` — `text`

### `dRegion`

- `Регион BI`
- `Порядок региона`
- `Группа региона`

### `dMonth`

- `MonthStart`
- `YearMonth`
- `MonthName`
- `MonthShort`
- `MonthLabel`
- `Название месяца`
- `Месяц коротко`
- `Месяц`
- `Год`
- `Месяц номер`
- `QuarterNum`
- `QuarterLabel`

### `page1_region_monthly_snapshot`

- `MonthStart`
- `YearMonth`
- `Регион BI`
- `Название месяца`
- `Месяц коротко`
- `Месяц`
- `Год`
- `Месяц номер`
- `KPI проекта %`
- `Качество визитов %`
- `Обязательное обучение %`
- `Фрод %`
- `Риск ухода структуры eNPS %`
- `Оценка команды %`
- `Индекс региона %`
- `Зона индекса`
- `Доступность метрик %`
- `Красных сигналов`
- `Мягких сигналов`
- `Красные сигналы`
- `Мягкие сигналы`
- `Статус региона`
- `Скоринг приоритета`
- `Ранг приоритета`
- `Текст приоритета`

### Логика статуса и приоритетов

- статус считается в ETL в `page1_region_monthly_snapshot`
- если `KPI проекта %` за месяц отсутствует, старый KPI не подставляется: вес перераспределяется между доступными операционными метриками
- если доступно меньше `60%` веса метрик → `Недостаточно данных`, регион не выводится в приоритеты
- `Высокий риск` — индекс региона ниже `80%`, либо 2+ красных сигнала, либо одновременный сильный кадровый отток и высокая текучесть
- `Контроль` — индекс региона `80–88%`, либо 1 красный сигнал, либо 2+ мягких сигнала
- `Стабильно` — индекс региона от `88%`, нет красных сигналов и меньше 2 мягких сигналов
- красные пороги: KPI `< 90%`, OKK `< 45%`, обучение `< 55%`, фрод `> 25%`, риск eNPS `> 30%`, вакансии `> 20%`, текучесть `> 15%`, кадровый отток `> 10`
- мягкие пороги: KPI `< 95%`, OKK `< 55%`, обучение `< 70%`, фрод `> 18%`, риск eNPS `> 22%`, вакансии `> 12%`, текучесть `> 8%`, кадровый отток `> 3`
- `Текст приоритета` заполняется только если есть хотя бы 1 сигнал
- `Ранг приоритета` считается только для регионов с непустым `Текст приоритета`
- стабильные регионы в правый блок не выводим

### `dQuarter`

- `QuarterStart`
- `YearQuarter`
- `QuarterLabel`

---

## 4. Проверенные связи модели

### Активные связи

- `dim_employees[ID сотрудника]` `1:*` `learning_fact[ID сотрудника]`
- `dim_employees[ID сотрудника]` `1:*` `fact_oed[ID сотрудника]`
- `dim_employees[ID сотрудника]` `1:*` `kpi_fact[ID мерчендайзера]`
- `dim_employees[ID сотрудника]` `1:*` `okk_fact[ID мерчендайзера]`

- `dMonth[MonthStart]` `1:*` `kpi_fact[MonthStart]`
- `dMonth[MonthStart]` `1:*` `okk_fact[MonthStart]`
- `dMonth[MonthStart]` `1:*` `learning_fact[MonthStart]`
- `dMonth[MonthStart]` `1:*` `learning_monthly[MonthStart]`

- `dQuarter[QuarterStart]` `1:*` `fact_oed[QuarterStart]`
- `dQuarter[QuarterStart]` `1:*` `enps_fact[QuarterStart]`

- `dRegion[Регион BI]` `1:*` `kpi_fact[Регион BI]`
- `dRegion[Регион BI]` `1:*` `okk_fact[Регион BI]`
- `dRegion[Регион BI]` `1:*` `learning_fact[Регион BI]`
- `dRegion[Регион BI]` `1:*` `learning_monthly[Регион BI]`
- `dRegion[Регион BI]` `1:*` `fact_oed[Регион BI]`
- `dRegion[Регион BI]` `1:*` `enps_fact[Регион BI]`

### Направление фильтрации

Везде ставим `Single`.

### Что не связывать

Не создавай активную связь:

- `dRegion[Регион BI]` → `dim_employees[Регион BI]`

Причина: тогда появятся двусмысленные пути фильтрации:

- `dRegion` → `fact_oed`
- `dRegion` → `dim_employees` → `fact_oed`

Это же касается `learning_fact`, `kpi_fact`, `okk_fact`.

---

## 5. Что это значит для мер

Поскольку `dRegion` не связан напрямую с `dim_employees`, все меры, которые считаются только по `dim_employees`, должны учитывать регион через `TREATAS`.

Это важно для:

- `m Активные сотрудники`
- мер обучения, если они итерируются по `dim_employees`

Меры, которые считаются прямо по фактам, `TREATAS` не требуют:

- KPI
- OKK
- фрод
- ОЭД
- риск ухода по eNPS
- месячный тренд обучения из `learning_monthly`

---

## 6. Проверенный набор мер

### Базовые меры

```DAX
m Активные сотрудники =
CALCULATE(
    COUNTROWS('dim_employees'),
    FILTER(
        'dim_employees',
        'dim_employees'[Активен] = TRUE()
    ),
    TREATAS(
        VALUES('dRegion'[Регион BI]),
        'dim_employees'[Регион BI]
    )
)
```

```DAX
m KPI проекта % =
CALCULATE(
    AVERAGE('kpi_fact'[KPI 1]),
    FILTER(
        'kpi_fact',
        'kpi_fact'[Вакансия] <> TRUE()
    )
)
```

```DAX
m Качество визитов % =
AVERAGE('okk_fact'[Качество визита])
```

```DAX
m Фрод % =
DIVIDE(
    CALCULATE(
        COUNTROWS('okk_fact'),
        FILTER(
            'okk_fact',
            'okk_fact'[Флаг фальсификации] = TRUE()
        )
    ),
    COUNTROWS('okk_fact')
)
```

```DAX
m Оценка команды % =
DIVIDE(
    AVERAGE('fact_oed'[Аттестация]),
    100
)
```

```DAX
m Риск ухода структуры % =
DIVIDE(
    COUNTROWS(
        FILTER(
            'enps_fact',
            'enps_fact'[Уровень риска ухода] = "Высокий"
        )
    ),
    COUNTROWS('enps_fact')
)
```

### Обязательное обучение

```DAX
m Обязательное обучение % =
VAR EmployeesToCalc =
    CALCULATETABLE(
        FILTER(
            'dim_employees',
            'dim_employees'[Активен] = TRUE()
        ),
        TREATAS(
            VALUES('dRegion'[Регион BI]),
            'dim_employees'[Регион BI]
        )
    )
RETURN
AVERAGEX(
    EmployeesToCalc,
    VAR PassedCount =
        CALCULATE(
            COUNTROWS('learning_fact'),
            'learning_fact'[Обязательный] = TRUE(),
            'learning_fact'[Пройдено] = TRUE()
        )
    VAR TotalCount =
        CALCULATE(
            COUNTROWS('learning_fact'),
            'learning_fact'[Обязательный] = TRUE()
        )
    RETURN
        DIVIDE(PassedCount, TotalCount)
)
```

### Месячный тренд обучения

Для графика используем не `learning_fact`, а отдельную витрину `learning_monthly`.

```DAX
m Обязательное обучение тренд % =
DIVIDE(
    SUM('learning_monthly'[Пройдено обязательных курсов]),
    SUM('learning_monthly'[Назначено обязательных курсов])
)
```

```DAX
m Обязательное обучение тренд % (закрытые месяцы) =
VAR CurrentAxisMonth = MAX('dMonth'[MonthStart])
VAR LastClosedMonth = EOMONTH(TODAY(), -1) + 1
RETURN
IF(
    CurrentAxisMonth >= LastClosedMonth,
    BLANK(),
    [m Обязательное обучение тренд %]
)
```

```DAX
m Обязательное обучение 100% % =
VAR EmployeesToCalc =
    CALCULATETABLE(
        FILTER(
            'dim_employees',
            'dim_employees'[Активен] = TRUE()
        ),
        TREATAS(
            VALUES('dRegion'[Регион BI]),
            'dim_employees'[Регион BI]
        )
    )
RETURN
AVERAGEX(
    EmployeesToCalc,
    VAR PassedCount =
        CALCULATE(
            COUNTROWS('learning_fact'),
            'learning_fact'[Обязательный] = TRUE(),
            'learning_fact'[Пройдено] = TRUE()
        )
    VAR TotalCount =
        CALCULATE(
            COUNTROWS('learning_fact'),
            'learning_fact'[Обязательный] = TRUE()
        )
    RETURN
        IF(
            TotalCount = 0,
            BLANK(),
            IF(PassedCount = TotalCount, 1, 0)
        )
)
```

### Подписи

```DAX
m Подпись KPI =
"факт KPI 1, " & SELECTEDVALUE('dMonth'[MonthLabel], "последний месяц")
```

```DAX
m Подпись ОКК =
"ОКК, " & SELECTEDVALUE('dMonth'[MonthLabel], "последний месяц")
```

```DAX
m Подпись ОЭД =
"аттестация / ОЭД, " & SELECTEDVALUE('dQuarter'[QuarterLabel], "последний квартал")
```

```DAX
m Подпись Обучение =
"завершено / назначено"
```

```DAX
m Подпись Риск =
"анонимный опрос"
```

### Статус региона

```DAX
m Статус региона =
VAR KPIValue = [m KPI проекта %]
VAR OKKValue = [m Качество визитов %]
VAR LearnValue = [m Обязательное обучение %]
VAR RiskValue = [m Риск ухода структуры %]
RETURN
SWITCH(
    TRUE(),
    RiskValue >= 0.18 || OKKValue < 0.45 || LearnValue < 0.65, "Высокий риск",
    RiskValue >= 0.12 || OKKValue < 0.55 || LearnValue < 0.75, "Контроль",
    "Стабильно"
)
```

```DAX
m Цвет статуса =
SWITCH(
    [m Статус региона],
    "Высокий риск", "#FCA5A5",
    "Контроль", "#FCD34D",
    "Стабильно", "#86EFAC",
    "#D1D5DB"
)
```

### Приоритеты справа

```DAX
m Скоринг приоритета =
VAR KPIValue = [m KPI проекта %]
VAR OKKValue = [m Качество визитов %]
VAR LearnValue = [m Обязательное обучение %]
VAR RiskValue = [m Риск ухода структуры %]
RETURN
    RiskValue * 100
    + (1 - OKKValue) * 10
    + (1 - LearnValue) * 8
    + (1 - KPIValue) * 6
```

```DAX
m Приоритет 1 =
VAR RegionTable =
    ADDCOLUMNS(
        ALLSELECTED('dRegion'[Регион BI]),
        "__KPI", [m KPI проекта %],
        "__OKK", [m Качество визитов %],
        "__Learn", [m Обязательное обучение %],
        "__Risk", [m Риск ухода структуры %],
        "__Score", [m Скоринг приоритета]
    )
VAR Ranked =
    ADDCOLUMNS(
        RegionTable,
        "__Rank", RANKX(RegionTable, [__Score],, DESC, Dense)
    )
VAR CurrentRow =
    FILTER(Ranked, [__Rank] = 1)
VAR RegionName =
    MAXX(CurrentRow, 'dRegion'[Регион BI])
VAR Message =
    MAXX(
        CurrentRow,
        SWITCH(
            TRUE(),
            [__Risk] >= 0.18, "высокий риск ухода структуры",
            [__OKK] < 0.45, "низкое качество визитов",
            [__Learn] < 0.65, "низкое обязательное обучение",
            [__KPI] < 0.70, "снижен KPI проекта",
            "требует внимания"
        )
    )
RETURN
IF(
    ISBLANK(RegionName),
    BLANK(),
    RegionName & ": " & Message & "."
)
```

```DAX
m Приоритет 2 =
VAR RegionTable =
    ADDCOLUMNS(
        ALLSELECTED('dRegion'[Регион BI]),
        "__KPI", [m KPI проекта %],
        "__OKK", [m Качество визитов %],
        "__Learn", [m Обязательное обучение %],
        "__Risk", [m Риск ухода структуры %],
        "__Score", [m Скоринг приоритета]
    )
VAR Ranked =
    ADDCOLUMNS(
        RegionTable,
        "__Rank", RANKX(RegionTable, [__Score],, DESC, Dense)
    )
VAR CurrentRow =
    FILTER(Ranked, [__Rank] = 2)
VAR RegionName =
    MAXX(CurrentRow, 'dRegion'[Регион BI])
VAR Message =
    MAXX(
        CurrentRow,
        SWITCH(
            TRUE(),
            [__Risk] >= 0.18, "высокий риск ухода структуры",
            [__OKK] < 0.45, "низкое качество визитов",
            [__Learn] < 0.65, "низкое обязательное обучение",
            [__KPI] < 0.70, "снижен KPI проекта",
            "требует внимания"
        )
    )
RETURN
IF(
    ISBLANK(RegionName),
    BLANK(),
    RegionName & ": " & Message & "."
)
```

```DAX
m Приоритет 3 =
VAR RegionTable =
    ADDCOLUMNS(
        ALLSELECTED('dRegion'[Регион BI]),
        "__KPI", [m KPI проекта %],
        "__OKK", [m Качество визитов %],
        "__Learn", [m Обязательное обучение %],
        "__Risk", [m Риск ухода структуры %],
        "__Score", [m Скоринг приоритета]
    )
VAR Ranked =
    ADDCOLUMNS(
        RegionTable,
        "__Rank", RANKX(RegionTable, [__Score],, DESC, Dense)
    )
VAR CurrentRow =
    FILTER(Ranked, [__Rank] = 3)
VAR RegionName =
    MAXX(CurrentRow, 'dRegion'[Регион BI])
VAR Message =
    MAXX(
        CurrentRow,
        SWITCH(
            TRUE(),
            [__Risk] >= 0.18, "высокий риск ухода структуры",
            [__OKK] < 0.45, "низкое качество визитов",
            [__Learn] < 0.65, "низкое обязательное обучение",
            [__KPI] < 0.70, "снижен KPI проекта",
            "требует внимания"
        )
    )
RETURN
IF(
    ISBLANK(RegionName),
    BLANK(),
    RegionName & ": " & Message & "."
)
```

---

## 7. Визуалы первой страницы

### Верхние карточки

- `KPI проекта` → `[m KPI проекта %]`
- `Качество визитов` → `[m Качество визитов %]`
- `Оценка команды` → `[m Оценка команды %]`
- `Обязательное обучение` → `[m Обязательное обучение %]`
- `Риск ухода структуры` → `[m Риск ухода структуры %]`

### График динамики

- Axis: `dMonth[MonthLabel]`
- Sort by: `dMonth[YearMonth]`
- Values:
  - `[m KPI проекта %]`
  - `[m Качество визитов %]`
  - `[m Обязательное обучение тренд % (закрытые месяцы)]`
  - `[m Фрод %]`

### Таблица регионов

- `dRegion[Регион BI]`
- `[m KPI проекта %]`
- `[m Качество визитов %]`
- `[m Обязательное обучение %]`
- `[m Фрод %]`
- `[m Риск ухода структуры %]`
- `[m Статус региона]`

Для условного форматирования статуса:

- цвет фона / pill → `[m Цвет статуса]`

### Блок `Приоритеты` справа

- лучше брать напрямую из `page1_region_monthly_snapshot`
- visual:
  - `Table`
- поле:
  - `page1_region_monthly_snapshot[Текст приоритета]`
- фильтры visual:
  - `Ранг приоритета <= 3`
  - `Текст приоритета is not blank`
- сортировка:
  - по `Ранг приоритета` ascending

---

## 8. Что осталось сделать в Power BI

- подключить parquet-таблицы из `data/out`
- создать связи ровно по схеме выше
- вставить проверенные меры
- собрать визуалы страницы

Это всё. Подготовка данных и служебных таблиц уже сделана в ETL.
