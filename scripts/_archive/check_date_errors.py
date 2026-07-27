import sys, pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

dim   = pd.read_parquet("data/out/dim_employees.parquet")
learn = pd.read_parquet("data/out/learning_fact.parquet")
fact  = pd.read_parquet("data/out/витрина_адаптации.parquet")

TODAY = pd.Timestamp("2026-06-03")

print("=== Скорость адаптации — распределение ===")
col = "Скорость адаптации (дней)"
print(fact[col].describe())
print()

# Отбираем подозрительные (>1000 дней или < -100)
suspicious_speed = fact[(fact[col] > 1000) | (fact[col] < -100)]
print(f"Подозрительная скорость адаптации (>1000 или <-100): {len(suspicious_speed)}")
print()

# Смотрим Дату приёма в dim_employees
print("=== Дата приёма в dim_employees ===")
dim_dates = dim[["ID сотрудника","ФИО","Дата приёма","Активен"]].copy()
dim_dates["Дата приёма"] = pd.to_datetime(dim_dates["Дата приёма"], errors="coerce")

# Проблемы:
# 1. Дата в будущем
future = dim_dates[dim_dates["Дата приёма"] > TODAY]
# 2. Дата до 2000 года (подозрительно старые)
ancient = dim_dates[dim_dates["Дата приёма"] < pd.Timestamp("2000-01-01")]
# 3. Null
no_date = dim_dates[dim_dates["Дата приёма"].isna()]
# 4. Очень давно (>10 лет = до 2016)
old     = dim_dates[(dim_dates["Дата приёма"] < pd.Timestamp("2016-01-01")) & dim_dates["Дата приёма"].notna()]

print(f"Дата в будущем (>{TODAY.date()}): {len(future)}")
print(f"До 2000 года:                      {len(ancient)}")
print(f"До 2016 года:                      {len(old)}")
print(f"Без даты:                          {len(no_date)}")
print()

# Собираем все проблемные
problematic_ids = set()
problematic_ids |= set(future["ID сотрудника"])
problematic_ids |= set(ancient["ID сотрудника"])

# Объединяем с данными обучения чтобы найти первопричину
learn_dates = learn.groupby("ID сотрудника")["Дата начала"].min().reset_index()
learn_dates.columns = ["ID сотрудника","Первый курс"]

report = dim_dates.copy()
report = report.merge(learn_dates, on="ID сотрудника", how="left")
report = report.merge(
    fact[["ID сотрудника","Скорость адаптации (дней)","Категория"]],
    on="ID сотрудника", how="left"
)

# Ошибки в дате: дата в будущем ИЛИ до 2000 ИЛИ дата приёма > дата первого курса + 365
report["Первый курс"] = pd.to_datetime(report["Первый курс"], errors="coerce")
report["Разрыв дней"] = (report["Дата приёма"] - report["Первый курс"]).dt.days

bad_date = report[
    (report["Дата приёма"] > TODAY) |
    (report["Дата приёма"] < pd.Timestamp("2000-01-01")) |
    (report["Разрыв дней"] > 365) |  # дата приёма на >1 год позже первого курса
    (report["Скорость адаптации (дней)"].abs() > 3000)
].copy()

print(f"=== Итоговый список с ошибками в дате: {len(bad_date)} ===")
print()
print(bad_date[["ID сотрудника","ФИО","Дата приёма","Первый курс",
                "Разрыв дней","Скорость адаптации (дней)","Активен"]].to_string())
print()

# Сохраняем в Excel
out_cols = ["ID сотрудника","ФИО","Дата приёма","Первый курс",
            "Разрыв дней","Скорость адаптации (дней)","Активен"]
bad_date[out_cols].to_excel("ошибки_дат_приема.xlsx", index=False)
print(f"Сохранено: ошибки_дат_приема.xlsx ({len(bad_date)} строк)")
