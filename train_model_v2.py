# train_model_v2.py
import pandas as pd
from datetime import datetime
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.naive_bayes import MultinomialNB
from sklearn.svm import LinearSVC
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.calibration import CalibratedClassifierCV
import joblib
import json

DATASET_PATH = Path("data/ml_dataset.csv")
MODEL_DIR = Path("data/ml")
MODEL_DIR.mkdir(parents=True, exist_ok=True)
METRICS_PATH = MODEL_DIR / "metrics.json"
# === Дополнительно: сохраняем историю метрик ===
HISTORY_PATH = MODEL_DIR / "f1_history.json"


def load_dataset():
    print("Загружаем датасет...")
    df = pd.read_csv(DATASET_PATH, on_bad_lines="skip", engine="python")
    df = df[df["is_ad"].astype(str).str.lower() != "is_ad"]
    df["is_ad"] = df["is_ad"].astype(str).str.replace("=", "").str.strip().astype(int)
    df = df.dropna(subset=["clean_text"])
    # фильтрация мусора
    df = df[df["clean_text"].str.len() >= 5]
    df = df[~df["clean_text"].str.match(r"^[0-9\s]+$")]
    print(f"Загружено {len(df)} сообщений.")
    return df


def main():
    df = load_dataset()
    if len(df) < 50:
        print("Недостаточно данных для обучения (<50 строк). Пропуск.")
        return

    X_train, X_test, y_train, y_test = train_test_split(
        df["clean_text"], df["is_ad"], test_size=0.2, random_state=42
    )

    print("Векторизация TF-IDF...")
    vectorizer = TfidfVectorizer(max_features=15000, ngram_range=(1, 2))
    X_train_tfidf = vectorizer.fit_transform(X_train)
    X_test_tfidf = vectorizer.transform(X_test)

    models = {
        "LogisticRegression": CalibratedClassifierCV(
            LogisticRegression(max_iter=1000), cv=3
        ),
        "MultinomialNB": CalibratedClassifierCV(MultinomialNB(), cv=3),
        "LinearSVC": CalibratedClassifierCV(LinearSVC(), method="sigmoid", cv=3),
        "SGDClassifier": CalibratedClassifierCV(
            SGDClassifier(loss="log_loss", max_iter=1000, class_weight="balanced"),
            cv=3,
        ),
    }

    best_model = None
    best_name = ""
    best_f1 = 0.0

    for name, model in models.items():
        print(f"\nМодель: {name}")
        model.fit(X_train_tfidf, y_train)
        y_pred = model.predict(X_test_tfidf)
        print(classification_report(y_test, y_pred, digits=3))
        f1 = f1_score(y_test, y_pred, pos_label=1)
        if f1 > best_f1:
            best_f1 = f1
            best_model = model
            best_name = name

    print(f"\nЛучшая модель: {best_name}")
    print(f"F1-score (реклама): {best_f1:.3f}")

    # === Проверяем предыдущую метрику ===
    old_f1 = 0.0
    if METRICS_PATH.exists():
        try:
            old_f1 = json.load(open(METRICS_PATH, encoding="utf-8")).get("f1", 0.0)
        except Exception:
            pass

    # === Сохраняем только если не хуже прошлой ===
    if best_f1 >= old_f1 - 0.02:
        joblib.dump(best_model, MODEL_DIR / "best_model.pkl")
        joblib.dump(vectorizer, MODEL_DIR / "vectorizer.pkl")
        json.dump(
            {"f1": best_f1, "model": best_name},
            open(METRICS_PATH, "w", encoding="utf-8"),
            ensure_ascii=False,
            indent=2,
        )
        print(f"Новая модель сохранена (F1={best_f1:.3f}).")
        append_f1_to_history(best_f1, best_name)
        notify_admin_if_changed(old_f1, best_f1, best_name)
    else:
        print(
            f"Новая модель хуже (F1={best_f1:.3f} < {old_f1:.3f}) — оставляем старую."
        )


def append_f1_to_history(f1_value: float, model_name: str):
    """Добавляет запись о новом обучении в историю метрик."""
    from datetime import datetime
    import json

    record = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "f1": round(float(f1_value), 3), 
        "model": model_name,
    }

    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []

    history.append(record)
    HISTORY_PATH.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"💾 Добавлено в историю F1: {record}")


def notify_admin_if_changed(old_f1, new_f1, model_name):
    import asyncio
    from aiogram import Bot
    from utils.config_loader import BOT_TOKEN
    from pathlib import Path
    import matplotlib.pyplot as plt
    import json
    import threading
    from datetime import datetime

    admin_id = 580759300  # твой Telegram ID
    diff = new_f1 - old_f1
    percent = (diff / old_f1 * 100) if old_f1 > 0 else 0

    if abs(diff) < 0.005:
        return  # почти без изменений — не уведомляем

    if diff > 0:
        status = "📈 <b>Улучшение модели</b>"
        arrow = "🟢"
    else:
        status = "📉 <b>Ухудшение модели</b>"
        arrow = "🔴"

    text = (
        f"{arrow} {status}\n\n"
        f"🧠 <b>Модель:</b> <code>{model_name}</code>\n"
        f"📊 <b>F1 старая:</b> {old_f1:.3f}\n"
        f"📊 <b>F1 новая:</b> {new_f1:.3f}\n"
        f"🔺 <b>Изменение:</b> {diff:+.3f} ({percent:+.1f}%)\n\n"
        f"⏱ Обновлено: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
    )

    # === Если есть история, строим график ===
    try:
        HISTORY_PATH = Path("data/ml/f1_history.json")
        CHART_PATH = Path("data/ml/f1_chart.png")

        if HISTORY_PATH.exists():
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if len(history) > 1:
                dates = [h["date"] for h in history]
                f1s = [h["f1"] for h in history]

                plt.figure(figsize=(6, 3))
                plt.plot(dates, f1s, marker="o", linewidth=2)
                plt.title("F1-Score динамика модели")
                plt.xlabel("Дата обучения")
                plt.ylabel("F1")
                plt.grid(True, linestyle="--", alpha=0.5)
                plt.tight_layout()
                plt.savefig(CHART_PATH, dpi=150)
                plt.close()
            else:
                CHART_PATH = None
        else:
            CHART_PATH = None
    except Exception as e:
        print(f"⚠️ Ошибка при построении графика F1: {e}")
        CHART_PATH = None

    # === Отправляем админу ===
    async def send():
        try:
            bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
            if CHART_PATH and CHART_PATH.exists():
                await bot.send_photo(
                    admin_id, photo=open(CHART_PATH, "rb"), caption=text
                )
            else:
                await bot.send_message(admin_id, text)
            await bot.session.close()
        except Exception as e:
            print(f"⚠️ Ошибка при отправке уведомления админу: {e}")

    threading.Thread(target=lambda: asyncio.run(send()), daemon=True).start()


if __name__ == "__main__":
    main()
