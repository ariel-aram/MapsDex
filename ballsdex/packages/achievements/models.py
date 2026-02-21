from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum
from io import BytesIO
from typing import TYPE_CHECKING, Iterable, Tuple, Type

import discord
from discord.utils import format_dt
from tortoise import exceptions, fields, models, signals, timezone, validators
from tortoise.contrib.postgres.indexes import PostgreSQLIndex
from tortoise.expressions import Q

from ballsdex.core.image_generator.image_gen import draw_card
from ballsdex.settings import settings

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient


class Achievement(models.Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)
    description = fields.TextField()
    achievement_emoji_id = fields.BigIntField(
            null=True,
            description="Discord emoji ID for achievement"
        )
    required_balls = fields.ManyToManyField(
        "models.Ball",
        related_name="achievements",
        through="achievements_required_balls",
        forward_key="achievement_id",
        backward_key="ball_id",
    )
    special_required = fields.ManyToManyField(
        "models.Special",
        related_name="special_achievements",
        through="achievements_special_required",
        forward_key="achievement_id",
        backward_key="special_id",
    )  
    required_quantity = fields.IntField(
        default=1,
        description="Number of each required combo (ball + special) needed"
    )  
    self_catch = fields.BooleanField(
       default=False, description="weither this achievement need self_catched balls"
    ) 
    reward = fields.ManyToManyField(
        "models.Ball",
        related_name="reward_achievements",
        through="achievements_reward",
        forward_key="achievement_id",
        backward_key="ball_id"
    ) 
    enable = fields.BooleanField(
        default=True, description="Disabled achievement will never be shown or can be claimed."
    )

    class Meta:
        table = "achievements"

    def __str__(self):
        return self.name

class PlayerAchievement(models.Model):
    id = fields.IntField(pk=True)
    player = fields.ForeignKeyField(
        "models.Player",
        on_delete=fields.CASCADE,
        related_name="player_achievements",
        db_column="player_id"
    )
    achievement = fields.ForeignKeyField(
        "models.Achievement",
        on_delete=fields.CASCADE,
        related_name="player_achievements",
        db_column="achievement_id"
    )
    unlocked_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "player_achievement"
        unique_together = (("player", "achievement"),)

    def __str__(self):
        return f"{self.player} → {self.achievement}"

class AchievementRequiredBall(models.Model):
    achievement = fields.ForeignKeyField(
        "models.Achievement",
        related_name="achievement_links",
        on_delete=fields.CASCADE,
        db_column="achievement_id",
    )
    ball = fields.ForeignKeyField(
        "models.Ball",
        related_name="ball_links",
        on_delete=fields.CASCADE,
        db_column="ball_id", 
        null=True
    )
    class Meta:
        table = "achievements_required_balls"
        unique_together = (("achievement", "ball"),)

    def __str__(self) -> str:
        return str(self.pk) 

class AchievementRequiredSpecial(models.Model):
    achievement = fields.ForeignKeyField(
        "models.Achievement",
        related_name="special_achievement_links",
        on_delete=fields.CASCADE,
        db_column="achievement_id",
    ) 
    special = fields.ForeignKeyField(
        "models.Special",
        related_name="special_ball_links",
        on_delete=fields.CASCADE,
        db_column="special_id",
        null=True
    ) 
    class Meta:
        table = "achievements_special_required"
        
    def __str__(self) -> str:                                                         
        return str(self.pk)
        
class AchievementReward(models.Model):
    id = fields.BigIntField(pk=True)
    achievement = fields.ForeignKeyField(
        "models.Achievement",
        related_name="reward_links",
        on_delete=fields.CASCADE,
        db_column="achievement_id",
    )
    ball = fields.ForeignKeyField(
        "models.Ball",
        related_name="achievement_reward_links",
        on_delete=fields.CASCADE, 
        db_column="ball_id",
    )

    class Meta:
        table = "achievements_reward"
        unique_together = (("achievement", "ball"),)

    def __str__(self) -> str:
        return f"{self.achievement} → {self.ball}"
