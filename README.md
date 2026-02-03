# LA Moderator Bot — ML-Powered Telegram Ad Moderation

> **Production ML system**  
> Real-time moderation of Telegram chats using a hybrid
> rule-based and machine learning approach for detecting
> advertising and commercial content.

This project is a production-ready Telegram moderation bot
used in real chat environments to automatically detect and handle
advertising, job offers, and commercial messages.

The bot combines:

- fast rule-based checks for obvious spam and ads,
- ML-based text classification for ambiguous cases,
- persistent storage for users, violations, and settings,
- operational tooling for retraining and maintenance.

The ML model itself is trained and evaluated in a separate project:  
👉 **telegram_ad_detection_ML**

---

## 🔍 Problem & Use Case

Large Telegram chats with hundreds of messages per day often suffer from:

- hidden or disguised advertising,
- repeated low-quality spam,
- job and commercial posts violating chat rules.

Manual moderation does not scale, while simple keyword-based filters:

- miss non-obvious ads,
- generate too many false positives,
- are hard to maintain over time.

**Goal:**  
Build an autonomous moderation system that:

- detects commercial content in noisy, multilingual text,
- applies consistent moderation rules in real time,
- tracks user violations across messages,
- supports whitelists and admin permissions,
- runs continuously in production.

---

## 🧠 System Overview

High-level processing pipeline:

1. **Telegram message handler**
   - receives updates from Telegram Bot API (`main.py`)
   - routes messages to handlers (`handlers/message_handler.py`)

2. **Rule-based pre-checks**
   - links, keywords, simple patterns
   - instant allow / deny decisions for obvious cases

3. **ML-based classification**
   - applied to ambiguous messages only
   - Logistic Regression + hybrid TF-IDF (word + char)
   - model artifacts stored in `data/ml/`:
     - `best_model.pkl`
     - `vectorizer.pkl`
     - `metrics.json`
     - `known_texts.txt`, `ads_text.txt`

4. **Decision layer**
   - threshold-based decisions (soft / balanced / strict)
   - chat-specific settings and limits
   - whitelist and admin bypass logic

5. **Actions**
   - delete message / allow message / log event
   - increment violation counters
   - optional admin notifications

6. **Persistence & logging**
   - SQLite database: `data/users.db`
   - JSON configs and ML artifacts in `data/`
   - runtime logs for monitoring and analysis

---

## 🤖 Machine Learning Component

The ML model is **not primarily trained inside this repository**.

Full ML experimentation, dataset preparation, labeling strategy,
model evaluation, and threshold tuning are implemented in:

👉 **telegram_ad_detection_ML**

This repository focuses on **production inference and integration**:

- loading trained model and vectorizer,
- performing fast inference on incoming messages,
- applying multiple threshold modes:
  - `SOFT` – higher recall, more false positives
  - `BALANCED` – trade-off between precision and recall
  - `STRICT` – higher precision, more false negatives

Operational ML scripts included here:

- `prepare_dataset.py` — build datasets from production logs / DB
- `train_model_v2.py` — retrain model on updated data
- `ml_autotrain.py` — automated retraining logic

---

## 🛡 Moderation Logic

Core moderation features:

- **Hybrid approach**
  - rule-based filters for obvious spam and ads
  - ML classifier for subtle or disguised content

- **Violation tracking**
  - per-user violation counters stored in SQLite
  - escalation logic for repeat offenders

- **Whitelist support**
  - approved advertisers or users
  - daily ad limits and usage tracking

- **Admin bypass**
  - bot ignores messages from admins and chat owners

- **Operational utilities**
  - `mark_ad.py`, `mark_not_ad.py` — manual labeling of edge cases
  - `reset_violations.py` — reset user violation counters
  - `restore_whitelist.py` — restore whitelist from backup

---

## 🧰 Tech Stack

- **Language:** Python 3.10+
- **Telegram API:** Python Telegram framework (aiogram-based)
- **ML Inference:** scikit-learn, joblib
- **Storage:** SQLite (`data/users.db`)
- **Configuration:** `.env`, `config.py`, JSON configs
- **Infrastructure:** Railway / VPS, persistent volumes
- **Logging:** Python logging for moderation and ML decisions

---

## 📂 Project Structure

Actual repository layout:

```text
LA_moderator_bot/
├── data/
│   ├── ml/
│   │   ├── best_model.pkl        # trained ML model (inference)
│   │   ├── vectorizer.pkl        # TF-IDF vectorizer
│   │   ├── metrics.json          # model metrics
│   │   ├── known_texts.txt       # reference texts
│   │   ├── ads_text.txt          # collected ad examples
│   │   └── config.json           # ML-related config
│   ├── users.db                  # SQLite database (runtime)
│   └── config.json               # general config (if used)
│
├── handlers/
│   ├── __init__.py
│   ├── admin_commands.py         # admin and owner commands
│   └── message_handler.py        # main moderation logic
│
├── utils/
│   ├── __init__.py
│   ├── config_loader.py          # env and config loading
│   └── db.py                     # SQLite helpers
│
├── .env.example
├── .gitignore
├── .python-version
├── LICENSE
├── Procfile
├── README.md
├── config.py
├── main.py                       # application entry point
├── mark_ad.py
├── mark_not_ad.py
├── ml_autotrain.py
├── prepare_dataset.py
├── requirements.txt
├── reset_violations.py
├── restore_whitelist.py
├── test.py
└── train_model_v2.py
```

> ℹ️ The structure reflects a real production system:  
> runtime bot code and operational ML scripts are
> intentionally kept together for deployment and maintenance convenience.

---

## 🚀 Quickstart (Local)

1. **Clone repository**
   ```bash
   git clone https://github.com/alnubvar/LA_moderator_bot.git
   cd LA_moderator_bot
   ```

2. Create virtual environment (optional)
  ```bash
  python -m venv venv
  # Linux / macOS
  source venv/bin/activate
  # Windows
  venv\Scripts\activate
  ```
3. Install dependencies
  ```bash
  pip install -r requirements.txt
  ```
4. Configure environment
   Create `.env` from `.env.example` and set:
- `BOT_TOKEN` — Telegram bot token  
- `ADMIN_IDS` — admin user IDs  
- `DB_PATH` — path to `data/users.db`  
- `ML_MODEL_DIR` / `ML_CONFIG_PATH` — path to `data/ml/`

5. Run the bot
  ```bash
  python main.py
  ```

---

## 🐳 Deployment (Railway / VPS)

Typical production setup:

- Bot code running in a container or VM  
- Persistent volume mounted for:
  - `data/users.db`
  - `data/ml/*.pkl`
  - backups
- Environment variables managed outside the repo  

This ensures:

- Database and ML model persistence across redeploys  
- Stable 24/7 operation  
- Safe model updates without data loss  

---

## 🧪 Monitoring & Logging

The bot logs:

- Moderation decisions (allow / delete)  
- ML confidence scores  
- Violation events and limits  
- Runtime and error information  

Logs are used to:

- Tune thresholds  
- Analyze false positives / false negatives  
- Collect new training data for retraining  

---

## 🧭 Future Improvements

- Admin dashboard for configuration and monitoring  
- Automatic scheduled database backups  
- A/B testing of thresholds and moderation modes  
- Optional transformer-based models behind feature flags  

---

## 👤 Author

Albert Nubaryan  
GitHub: [https://github.com/alnubvar](https://github.com/alnubvar)  
Email: [alnubwork@gmail.com](mailto:alnubwork@gmail.com)
