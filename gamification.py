"""Геймификация: очки, достижения, leaderboard.

Система мотивации пользователей через:
- Очки за точность входов
- Достижения (streaks, best trade, milestones)
- Leaderboard среди premium-подписчиков
- Уровни (новичок → трейдер → про → мастер)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

GAMIFICATION_PATH = os.path.join(os.path.dirname(__file__), "gamification.json")


@dataclass
class Achievement:
    """Достижение."""
    id: str
    name: str
    description: str
    points: int
    icon: str
    condition: str  # описание условия


@dataclass
class UserStats:
    """Статистика пользователя."""
    user_id: str
    points: int
    level: int
    level_name: str
    total_trades: int
    winning_trades: int
    current_streak: int
    best_streak: int
    best_trade_r: float
    total_r: float
    achievements: list[str]
    last_updated: float

    @property
    def winrate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades


# Предопределённые достижения
ACHIEVEMENTS = {
    "first_trade": Achievement(
        id="first_trade",
        name="Первый шаг",
        description="Соверши первую сделку",
        points=10,
        icon="🎯",
        condition="Соверши 1 сделку"
    ),
    "win_streak_3": Achievement(
        id="win_streak_3",
        name="Серия побед",
        description="3 прибыльные сделки подряд",
        points=50,
        icon="🔥",
        condition="3 сделки подряд в плюс"
    ),
    "win_streak_5": Achievement(
        id="win_streak_5",
        name="На волне",
        description="5 прибыльных сделок подряд",
        points=100,
        icon="🌊",
        condition="5 сделок подряд в плюс"
    ),
    "win_streak_10": Achievement(
        id="win_streak_10",
        name="Легенда",
        description="10 прибыльных сделок подряд",
        points=300,
        icon="👑",
        condition="10 сделок подряд в плюс"
    ),
    "big_win": Achievement(
        id="big_win",
        name="Джекпот",
        description="Сделка с прибылью >5R",
        points=75,
        icon="💎",
        condition="Одна сделка >5R"
    ),
    "perfect_month": Achievement(
        id="perfect_month",
        name="Идеальный месяц",
        description="Winrate >80% за месяц",
        points=200,
        icon="🏆",
        condition="Winrate >80% за 30 дней"
    ),
    "consistent": Achievement(
        id="consistent",
        name="Стабильность",
        description="30 сделок с winrate >60%",
        points=150,
        icon="📈",
        condition="30 сделок, winrate >60%"
    ),
    "trader_100": Achievement(
        id="trader_100",
        name="Опытный",
        description="100 сделок",
        points=100,
        icon="🎓",
        condition="100 сделок"
    ),
    "trader_500": Achievement(
        id="trader_500",
        name="Мастер",
        description="500 сделок",
        points=500,
        icon="🏅",
        condition="500 сделок"
    ),
    "millionaire": Achievement(
        id="millionaire",
        name="Миллионер",
        description="Общая прибыль >100R",
        points=500,
        icon="💰",
        condition="Сумма R >100"
    ),
}


LEVELS = [
    (0, "Новичок", 0),
    (100, "Ученик", 1),
    (500, "Трейдер", 2),
    (1000, "Про", 3),
    (2500, "Мастер", 4),
    (5000, "Эксперт", 5),
    (10000, "Гуру", 6),
    (25000, "Легенда", 7),
]


class GamificationSystem:
    """Система геймификации."""

    def __init__(self):
        self._users: dict[str, UserStats] = {}
        self._load()

    def _load(self):
        """Загрузить данные."""
        try:
            with open(GAMIFICATION_PATH, encoding="utf-8") as f:
                data = json.load(f)
                for uid, stats in data.items():
                    self._users[uid] = UserStats(**stats)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        """Сохранить данные."""
        data = {uid: asdict(stats) for uid, stats in self._users.items()}
        with open(GAMIFICATION_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_user_stats(self, user_id: str) -> UserStats:
        """Получить статистику пользователя."""
        if user_id not in self._users:
            self._users[user_id] = UserStats(
                user_id=user_id,
                points=0,
                level=0,
                level_name="Новичок",
                total_trades=0,
                winning_trades=0,
                current_streak=0,
                best_streak=0,
                best_trade_r=0.0,
                total_r=0.0,
                achievements=[],
                last_updated=time.time(),
            )
        return self._users[user_id]

    def add_trade_result(self, user_id: str, r: float, signal_data: dict | None = None):
        """Добавить результат сделки."""
        stats = self.get_user_stats(user_id)

        stats.total_trades += 1
        stats.total_r += r
        stats.last_updated = time.time()

        if r > 0:
            stats.winning_trades += 1
            stats.current_streak += 1
            stats.best_streak = max(stats.best_streak, stats.current_streak)
            stats.best_trade_r = max(stats.best_trade_r, r)

            # Очки за прибыльную сделку
            points = int(r * 10)  # 10 очков за 1R
            stats.points += points
        else:
            stats.current_streak = 0
            # Штраф за убыточную сделку
            stats.points = max(0, stats.points - 5)

        # Проверка достижений
        self._check_achievements(stats, r)

        # Обновление уровня
        self._update_level(stats)

        self._save()

    def _check_achievements(self, stats: UserStats, r: float):
        """Проверить и выдать достижения."""
        for ach_id, ach in ACHIEVEMENTS.items():
            if ach_id in stats.achievements:
                continue

            unlocked = False

            if ach_id == "first_trade" and stats.total_trades >= 1:
                unlocked = True
            elif ach_id == "win_streak_3" and stats.current_streak >= 3:
                unlocked = True
            elif ach_id == "win_streak_5" and stats.current_streak >= 5:
                unlocked = True
            elif ach_id == "win_streak_10" and stats.current_streak >= 10:
                unlocked = True
            elif ach_id == "big_win" and r >= 5.0:
                unlocked = True
            elif ach_id == "trader_100" and stats.total_trades >= 100:
                unlocked = True
            elif ach_id == "trader_500" and stats.total_trades >= 500:
                unlocked = True
            elif ach_id == "millionaire" and stats.total_r >= 100.0:
                unlocked = True
            elif ach_id == "consistent" and stats.total_trades >= 30 and stats.winrate >= 0.6:
                unlocked = True

            if unlocked:
                stats.achievements.append(ach_id)
                stats.points += ach.points

    def _update_level(self, stats: UserStats):
        """Обновить уровень пользователя."""
        for points, name, level in LEVELS:
            if stats.points >= points:
                stats.level = level
                stats.level_name = name

    def get_leaderboard(self, limit: int = 10) -> list[UserStats]:
        """Получить leaderboard (сортировка по очкам)."""
        sorted_users = sorted(
            self._users.values(),
            key=lambda x: x.points,
            reverse=True
        )
        return sorted_users[:limit]

    def get_user_rank(self, user_id: str) -> int:
        """Получить ранг пользователя в leaderboard."""
        sorted_users = sorted(
            self._users.values(),
            key=lambda x: x.points,
            reverse=True
        )
        for i, stats in enumerate(sorted_users):
            if stats.user_id == user_id:
                return i + 1
        return len(sorted_users) + 1

    def add_quiz_points(self, user_id: str, points: int):
        """Добавить очки за викторину."""
        stats = self.get_user_stats(user_id)
        stats.points += points
        self._update_level(stats)
        self._save()

    def get_all_achievements(self) -> dict[str, Achievement]:
        """Получить все достижения."""
        return ACHIEVEMENTS.copy()


# Глобальный инстанс
gamification = GamificationSystem()
