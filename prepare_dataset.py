# prepare_dataset.py
import sqlite3
import pandas as pd
import re
from pathlib import Path

DB_PATH = Path("data/users.db")
CSV_PATH = Path("data/ml_dataset.csv")
KNOWN_PATH = Path("data/ml/known_texts.txt")


def filter_known_texts(df):
    """Отфильтровывает уже известные тексты (из прошлых обучений)."""
    if not KNOWN_PATH.exists():
        return df
    known = set(
        line.strip() for line in KNOWN_PATH.read_text(encoding="utf-8").splitlines()
    )
    df = df[~df["clean_text"].isin(known)]
    return df


def clean_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.lower()
    s = re.sub(r"http\S+|www\S+|t\.me/\S+", " ", s)
    s = re.sub(r"@\w+", " ", s)
    s = re.sub(r"[^a-zа-я0-9\s]", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def read_db() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    df = pd.read_sql_query(
        """
        SELECT
            m.id            AS id,
            m.username      AS username,
            m.text          AS text,
            m.is_ad         AS is_ad
        FROM messages m
        WHERE m.text IS NOT NULL AND LENGTH(TRIM(m.text)) > 0
        """,
        conn,
    )
    conn.close()

    df["is_ad"] = df["is_ad"].fillna(0).astype(int)
    df["clean_text"] = df["text"].astype(str).apply(clean_text)
    # фильтруем мусор
    df = df[df["clean_text"].str.len() >= 5]
    df = df[~df["clean_text"].str.match(r"^[0-9\s]+$")]
    return df


def read_csv_safe() -> pd.DataFrame:
    if not CSV_PATH.exists():
        return pd.DataFrame(columns=["id", "username", "text", "is_ad", "clean_text"])
    df = pd.read_csv(CSV_PATH, on_bad_lines="skip", engine="python")
    if "clean_text" not in df.columns:
        df["clean_text"] = df["text"].astype(str).apply(clean_text)
    df["is_ad"] = df["is_ad"].fillna(0).astype(int)
    df = df[df["clean_text"].str.len() >= 5]
    df = df[~df["clean_text"].str.match(r"^[0-9\s]+$")]
    return df


def merge_priority_ad(df_all: pd.DataFrame) -> pd.DataFrame:
    """Если текст встречается с разными метками — побеждает реклама."""
    agg = (
        df_all.sort_values("is_ad", ascending=False)
        .groupby("clean_text", as_index=False)
        .agg({"is_ad": "max", "id": "first", "username": "first", "text": "first"})
    )
    agg = agg[["id", "username", "text", "is_ad", "clean_text"]]
    agg = agg.reset_index(drop=True)
    return agg


def main():
    print("Обновление датасета...")

    # === 1) Загружаем старый CSV ===
    df_csv = read_csv_safe()
    print(f"   • Старых строк: {len(df_csv)}")

    # === 2) Тянем из базы ВСЕ сообщения ===
    df_db = read_db()
    print(f"   • Из БД пришло: {len(df_db)}")

    # === 3) Объединяем старое + новое ===
    df_all = pd.concat([df_csv, df_db], ignore_index=True)

    # убираем дубликаты
    df_all = df_all.drop_duplicates(subset=["clean_text", "is_ad"])

    # === 4) Разруливаем споры (если текст встречался с разными метками) ===
    df_final = merge_priority_ad(df_all)

    # === 5) Отфильтровываем известные тексты ТОЛЬКО для журнала ===
    before_filter = len(df_final)
    new_rows = filter_known_texts(df_final)
    new_texts = len(new_rows)
    print(f"   • Новых уникальных текстов для журнала: {new_texts} из {before_filter}")

    # === 6) Сохраняем новые clean_text в known_texts, но оставляем датасет полный ===
    if new_texts > 0:
        KNOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with KNOWN_PATH.open("a", encoding="utf-8") as f:
            for text in new_rows["clean_text"].tolist():
                f.write(text + "\n")

    # === 7) Сохраняем итоговый датасет ===
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_final.to_csv(CSV_PATH, index=False, encoding="utf-8")

    added = len(df_final) - len(df_csv)
    print(f"Добавлено новых записей: {added}, всего теперь {len(df_final)} строк.")
    print(f"Сохранено: {CSV_PATH}")


if __name__ == "__main__":
    main()
