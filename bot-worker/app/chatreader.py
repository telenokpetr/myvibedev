"""Чтение сообщений чата Zoom.

Две стратегии: AT-SPI (дерево доступности, основная) и OCR панели чата (запасная).
Реальное извлечение текста зависит от версии клиента и раскладки окна —
калибруется по скриншотам живого митинга (задача #8). Здесь — интерфейс и каркас,
чтобы движок модерации работал и тестировался независимо от чтения.
"""

from dataclasses import dataclass


@dataclass
class ChatMessage:
    sender: str
    text: str


class ChatReader:
    def available(self) -> bool:
        return False

    def poll(self) -> list[ChatMessage]:
        """Вернуть новые сообщения с прошлого вызова."""
        return []


class AtspiChatReader(ChatReader):
    """Через AT-SPI. TODO(калибровка): найти узел панели чата и читать новые строки."""

    def available(self) -> bool:
        try:
            import pyatspi  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def poll(self) -> list[ChatMessage]:
        return []


class OcrChatReader(ChatReader):
    """OCR области чата (tesseract). TODO(калибровка): координаты области + дедуп строк."""

    def poll(self) -> list[ChatMessage]:
        return []


def get_reader() -> ChatReader:
    atspi = AtspiChatReader()
    if atspi.available():
        return atspi
    return OcrChatReader()
