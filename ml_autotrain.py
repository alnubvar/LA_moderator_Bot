# ml_autotrain.py
import subprocess
import sys
from datetime import datetime
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding="utf-8")

# Пути
DATA_DIR = Path("data")
LOG_PATH = DATA_DIR / "ml_autotrain.log"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    """Пишет в лог и в консоль."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def run_step(script_name: str) -> int:
    """Запускает подпроцесс Python и пишет stdout/stderr в лог."""
    cmd = [sys.executable, script_name]
    log(f"Запуск: {script_name}")
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    if process.stdout.strip():
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n[STDOUT — {script_name}]\n{process.stdout}\n")

    if process.stderr.strip():
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n[STDERR — {script_name}]\n{process.stderr}\n")

    if process.returncode == 0:
        log(f"✅ {script_name} завершён успешно.")
    else:
        log(f"Ошибка при выполнении {script_name} (код {process.returncode})!")
    log("-" * 80)

    return process.returncode

def main():
    log("Автообучение ML: запуск пайплайна.")
    start_time = datetime.now()

    # === 1️⃣ Подготовка датасета ===
    if run_step("prepare_dataset.py") != 0:
        log("Ошибка на этапе подготовки датасета — обучение остановлено.")
        return

    # === 2️⃣ Обучение модели ===
    if run_step("train_model_v2.py") != 0:
        log("Ошибка на этапе обучения модели — см. лог выше.")
        return

    # === 3️⃣ Финал ===
    elapsed = (datetime.now() - start_time).total_seconds()
    log(f"Автообучение завершено успешно за {elapsed:.1f} сек.")
    log("=" * 80 + "\n")

if __name__ == "__main__":
    main()
