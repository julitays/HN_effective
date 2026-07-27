# Power BI: blueprint шестой страницы

## 1. Что уже сделано в ETL

- собрана витрина `page6_okk_region_monthly.parquet`
- собрана единая витрина `page6_okk_insights_monthly.parquet`

---

## 2. Что подключать в Power BI

### Размерности

- `dMonth` → `data/out/dMonth.parquet`
- `dRegion` → `data/out/dRegion.parquet`

### Витрины листа 6

- `page6_okk_region_monthly` → `data/out/page6_okk_region_monthly.parquet`
- `page6_okk_insights_monthly` → `data/out/page6_okk_insights_monthly.parquet`

---

## 3. Проверенные поля

### `page6_okk_region_monthly`

- `MonthStart`
- `YearMonth`
- `Регион BI`
- `ОКК %`
- `Фрод %`
- `OSA %`
- `PICOS %`
- `KPI проекта %`
- `Фрод кол-во`

### `page6_okk_insights_monthly`

- `MonthStart`
- `YearMonth`
- `Регион BI`
- `Тип блока`
- `Категория`
- `Показатель`
- `% из проверок ОКК`
- `KPI-разрыв %`
- `Фрод %`
- `Просадка ОКК %`
- `Доля нарушения %`
- `Объект`
- `Риск`
- `Действие`
- `Порядок`

---

## 4. Связи модели

- `dMonth[MonthStart]` `1:*` `page6_okk_region_monthly[MonthStart]`
- `dMonth[MonthStart]` `1:*` `page6_okk_insights_monthly[MonthStart]`
- `dRegion[Регион BI]` `1:*` `page6_okk_region_monthly[Регион BI]`
- `dRegion[Регион BI]` `1:*` `page6_okk_insights_monthly[Регион BI]`

---

## 5. Как собирать визуалы

### Верхний левый график — динамика качества ОКК по регионам

- visual: `Линейный график`
- источник: `page6_okk_region_monthly`
- ось X:
  - `dMonth[MonthLabel]`
- значения:
  - `page6_okk_region_monthly[ОКК %]`
- легенда:
  - `page6_okk_region_monthly[Регион BI]`
- важно:
  - `MonthLabel` сортировать по `dMonth[YearMonth]`
  - если на странице выбран 1 регион, останется 1 линия

### Верхняя правая таблица — блоки анкеты ОКК и связь с KPI

- visual: `Table`
- источник: `page6_okk_insights_monthly`
- visual-level filter:
  - `Тип блока = Аномалия`
- поля:
  - `Категория` → переименовать в `Блок`
  - `Показатель` → переименовать в `Метрика`
  - `% из проверок ОКК`
  - `KPI-разрыв %`
  - `Фрод %`
  - `Объект` → переименовать в `Зона`

### Нижний левый график — влияние нарушений на KPI/качество

- visual: `Линейчатая диаграмма`
- источник: `page6_okk_insights_monthly`
- visual-level filter:
  - `Тип блока = Влияние`
- ось Y:
  - `Показатель`
- значения:
  - `Просадка ОКК %`
- важно:
  - агрегирование = `Среднее`
  - сортировка по `Просадка ОКК %` по убыванию

### Нижняя правая таблица — сигналы ОКК

- visual: `Table`
- источник: `page6_okk_insights_monthly`
- visual-level filter:
  - `Тип блока = Сигнал`
- поля:
  - `Показатель` → переименовать в `Сигнал`
  - `Объект`
  - `Риск`
  - `Действие`

---

## 6. Логика ETL

- `page6_okk_region_monthly` строится по `месяц + регион`
- `ОКК %` = среднее `Качество визита`
- `Фрод %` = среднее `Флаг фальсификации`
- `OSA %` = среднее `% наличия товара на полке`
- `PICOS %` = среднее `% наличия PICoS`
- `KPI проекта %` подтягивается из `page1_region_monthly_snapshot`

- `page6_okk_insights_monthly` объединяет три логики в одну таблицу:
  - `Аномалия` — худший блок анкеты по месяцу
  - `Влияние` — насколько нарушение просаживает качество визита: среднее ОКК без нарушения минус среднее ОКК с нарушением
  - `Сигнал` — готовые управленческие сигналы

---

## 7. Что важно

- для этого листа достаточно 2 витрин
- верхний левый график живет на отдельной гранулярности `месяц + регион`
- все остальные визуалы живут на единой витрине `page6_okk_insights_monthly`
- heavy DAX для страницы не нужен
