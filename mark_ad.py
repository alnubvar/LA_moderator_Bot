# mark_ad.py
import sqlite3
import pandas as pd
import sys
from pathlib import Path

DB_PATH = Path("data/users.db")
CSV_PATH = Path("data/ml_dataset.csv")

def mark_as_ad(ids):
    """Обновляет is_ad=1 в БД для заданных id"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # обновляем в таблице messages
    for msg_id in ids:
        cur.execute("UPDATE messages SET is_ad = 1 WHERE id = ?", (msg_id,))
    conn.commit()

    # Проверим, сколько реально обновлено
    cur.execute(
        f"SELECT id, username, text FROM messages WHERE id IN ({','.join(['?']*len(ids))})",
        ids
    )
    rows = cur.fetchall()
    conn.close()

    print(f"✅ Обновлено {len(rows)} записей в БД:")
    for row in rows:
        print(f"   → id={row[0]}, username={row[1]}, text={row[2][:60]!r}...")

    return rows

def sync_to_csv(rows):
    """Синхронизирует изменения в ml_dataset.csv"""
    if not CSV_PATH.exists():
        print("⚠️ Файл ml_dataset.csv не найден, создайте его сначала (prepare_dataset.py).")
        return

    df = pd.read_csv(CSV_PATH, on_bad_lines="skip", engine="python")
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)

    updated = 0
    for row in rows:
        msg_id = row[0]
        if msg_id in df["id"].values:
            df.loc[df["id"] == msg_id, "is_ad"] = 1
            updated += 1
        else:
            # если нет в датасете, добавим
            new_row = {
                "id": msg_id,
                "username": row[1],
                "text": row[2],
                "is_ad": 1,
                "clean_text": str(row[2]).lower()
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            updated += 1

    df.to_csv(CSV_PATH, index=False)
    print(f"💾 Обновлён ml_dataset.csv ({updated} строк синхронизировано).")

def main():
    if len(sys.argv) < 2:
        print("⚙️ Использование: python mark_ad.py <id1> [id2 id3 ...]")
        print("   Пример: python mark_ad.py 145 203 426")
        return

    try:
        ids = [int(x) for x in sys.argv[1:]]
    except ValueError:
        print("❌ ID должны быть числами.")
        return

    print(f"🔍 Помечаем сообщения как рекламу: {ids}")
    rows = mark_as_ad(ids)
    if rows:
        sync_to_csv(rows)
    print("✅ Готово.")

if __name__ == "__main__":
    main()
