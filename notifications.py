"""Система уведомлений: настройки частоты, персонализация, приоритеты.

Поддерживает:
- Настройку частоты уведомлений (все, важные, дайджест)
- Персонализацию на основе истории пользователя
- Приоритеты уведомлений
- Каналы доставки (push, email, Telegram)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import config as cfg
import logger_config

NOTIFICATIONS_PATH = os.path.join(os.path.dirname(__file__), "notifications.json")


class NotificationPriority(str, Enum):
    """Приоритет уведомления."""
    CRITICAL = "critical"  # Срочно: стоп, срочные алерты
    HIGH = "high"  # Важно: новые сигналы, цели
    NORMAL = "normal"  # Обычное: обновления статуса
    LOW = "low"  # Низкое: информационные


class NotificationFrequency(str, Enum):
    """Частота уведомлений."""
    ALL = "all"  # Все уведомления
    IMPORTANT = "important"  # Только важные (CRITICAL + HIGH)
    DIGEST = "digest"  # Дайджест раз в день
    NONE = "none"  # Отключены


class NotificationChannel(str, Enum):
    """Канал доставки."""
    TELEGRAM = "telegram"
    EMAIL = "email"
    PUSH = "push"


@dataclass
class NotificationRule:
    """Правило уведомления для пользователя."""
    user_id: int
    frequency: NotificationFrequency
    channels: list[NotificationChannel]
    min_priority: NotificationPriority
    quiet_hours_start: str | None = None  # "22:00"
    quiet_hours_end: str | None = None  # "08:00"
    digest_time: str = "09:00"  # Время отправки дайджеста


@dataclass
class Notification:
    """Уведомление."""
    id: str
    user_id: int
    title: str
    body: str
    priority: NotificationPriority
    channel: NotificationChannel
    data: dict[str, Any] | None = None
    created_at: str = None
    sent_at: str | None = None
    read: bool = False

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()


class NotificationManager:
    """Менеджер уведомлений."""

    def __init__(self):
        self._rules: dict[int, NotificationRule] = {}
        self._queue: list[Notification] = []
        self._load_rules()

    def _load_rules(self):
        """Загрузить правила из файла."""
        try:
            with open(NOTIFICATIONS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                for uid, rule_data in data.items():
                    self._rules[int(uid)] = NotificationRule(
                        user_id=int(uid),
                        frequency=NotificationFrequency(rule_data["frequency"]),
                        channels=[NotificationChannel(c) for c in rule_data["channels"]],
                        min_priority=NotificationPriority(rule_data["min_priority"]),
                        quiet_hours_start=rule_data.get("quiet_hours_start"),
                        quiet_hours_end=rule_data.get("quiet_hours_end"),
                        digest_time=rule_data.get("digest_time", "09:00"),
                    )
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_rules(self):
        """Сохранить правила в файл."""
        data = {
            str(uid): asdict(rule) for uid, rule in self._rules.items()
        }
        with open(NOTIFICATIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def set_rule(self, user_id: int, rule: NotificationRule):
        """Установить правило для пользователя."""
        self._rules[user_id] = rule
        self._save_rules()
        logger_config.metrics.increment("notification_rule_set")

    def get_rule(self, user_id: int) -> NotificationRule | None:
        """Получить правило пользователя."""
        return self._rules.get(user_id)

    def remove_rule(self, user_id: int):
        """Удалить правило пользователя."""
        self._rules.pop(user_id, None)
        self._save_rules()

    def should_send(self, notification: Notification) -> bool:
        """Проверить, нужно ли отправить уведомление."""
        rule = self._rules.get(notification.user_id)
        if not rule:
            # Если правило не задано, используем настройки по умолчанию
            return notification.priority in (NotificationPriority.CRITICAL, NotificationPriority.HIGH)

        # Проверка по частоте
        if rule.frequency == NotificationFrequency.NONE:
            return False
        elif rule.frequency == NotificationFrequency.IMPORTANT:
            if notification.priority not in (NotificationPriority.CRITICAL, NotificationPriority.HIGH):
                return False
        elif rule.frequency == NotificationFrequency.DIGEST:
            # Для дайджеста не отправляем сразу, добавляем в очередь
            return False

        # Проверка приоритета
        priority_order = {
            NotificationPriority.CRITICAL: 4,
            NotificationPriority.HIGH: 3,
            NotificationPriority.NORMAL: 2,
            NotificationPriority.LOW: 1,
        }
        if priority_order[notification.priority] < priority_order[rule.min_priority]:
            return False

        # Проверка quiet hours
        if rule.quiet_hours_start and rule.quiet_hours_end:
            now = datetime.utcnow()
            try:
                start = datetime.strptime(rule.quiet_hours_start, "%H:%M").time()
                end = datetime.strptime(rule.quiet_hours_end, "%H:%M").time()
                current_time = now.time()

                # Если текущее время в quiet hours
                if start < end:
                    if start <= current_time <= end:
                        return False
                else:  # Переход через полночь
                    if current_time >= start or current_time <= end:
                        return False
            except ValueError:
                pass

        return True

    def send(self, notification: Notification) -> bool:
        """Отправить уведомление."""
        if not self.should_send(notification):
            logger_config.metrics.increment("notification_filtered")
            return False

        # Здесь была бы отправка через нужный канал
        # Для Telegram используем существующий tg.send_message
        if notification.channel == NotificationChannel.TELEGRAM:
            try:
                import tg
                message = f"<b>{notification.title}</b>\n\n{notification.body}"
                ok, _ = tg.send_message(message, chat_id=notification.user_id)
                if ok:
                    notification.sent_at = datetime.utcnow().isoformat()
                    logger_config.metrics.increment("notification_sent")
                    return True
            except Exception as e:
                logger_config.log.error(f"Failed to send notification: {e}")
                logger_config.metrics.increment("notification_failed")

        return False

    def add_to_digest(self, notification: Notification):
        """Добавить уведомление в дайджест."""
        self._queue.append(notification)
        logger_config.metrics.increment("notification_queued")

    def send_digest(self, user_id: int) -> int:
        """Отправить дайджест уведомлений пользователю."""
        rule = self._rules.get(user_id)
        if not rule or rule.frequency != NotificationFrequency.DIGEST:
            return 0

        user_notifications = [n for n in self._queue if n.user_id == user_id]
        if not user_notifications:
            return 0

        # Формируем дайджест
        lines = ["📊 <b>Дайджест уведомлений</b>\n"]
        for notif in user_notifications:
            lines.append(f"\n• {notif.title}")
            lines.append(f"  {notif.body}")

        try:
            import tg
            message = "\n".join(lines)
            ok, _ = tg.send_message(message, chat_id=user_id)
            if ok:
                # Удаляем отправленные из очереди
                self._queue = [n for n in self._queue if n.user_id != user_id]
                logger_config.metrics.increment("digest_sent")
                return len(user_notifications)
        except Exception as e:
            logger_config.log.error(f"Failed to send digest: {e}")
            logger_config.metrics.increment("digest_failed")

        return 0

    def get_user_stats(self, user_id: int) -> dict:
        """Получить статистику уведомлений пользователя."""
        rule = self._rules.get(user_id)
        queued = len([n for n in self._queue if n.user_id == user_id])

        return {
            "rule": asdict(rule) if rule else None,
            "queued": queued,
        }


# Глобальный инстанс менеджера
notification_manager = NotificationManager()


def send_signal_notification(user_id: int, signal: dict) -> bool:
    """Отправить уведомление о новом сигнале."""
    notification = Notification(
        id=f"signal_{signal.get('id', '')}_{user_id}",
        user_id=user_id,
        title=f"🚀 Новый сигнал: {signal.get('side', '')} {signal.get('symbol', '')}",
        body=f"Вход: {signal.get('entry')}\nСтоп: {signal.get('sl')}\nЦель: {signal.get('tp1')}",
        priority=NotificationPriority.HIGH,
        channel=NotificationChannel.TELEGRAM,
        data=signal,
    )
    return notification_manager.send(notification)


def send_trade_result_notification(user_id: int, result: dict) -> bool:
    """Отправить уведомление о результате сделки."""
    emoji = "🎯" if result.get("kind", "").startswith("tp") else "🛑"
    notification = Notification(
        id=f"trade_{result.get('id', '')}_{user_id}",
        user_id=user_id,
        title=f"{emoji} Сделка закрыта: {result.get('kind', '')}",
        body=f"Результат: {result.get('r', 0):.2f}R\nСимвол: {result.get('symbol', '')}",
        priority=NotificationPriority.HIGH,
        channel=NotificationChannel.TELEGRAM,
        data=result,
    )
    return notification_manager.send(notification)


def send_critical_alert(user_id: int, title: str, body: str) -> bool:
    """Отправить критическое уведомление."""
    notification = Notification(
        id=f"critical_{datetime.utcnow().timestamp()}_{user_id}",
        user_id=user_id,
        title=f"⚠️ {title}",
        body=body,
        priority=NotificationPriority.CRITICAL,
        channel=NotificationChannel.TELEGRAM,
    )
    return notification_manager.send(notification)
