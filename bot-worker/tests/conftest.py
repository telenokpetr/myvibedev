"""Общая настройка тестов bot-worker.

Секрет задаём ДО импорта app.* — config читает переменные окружения на импорте.
"""

import os

os.environ.setdefault("INTERNAL_API_TOKEN", "test-secret")
