from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, cast

from django.contrib import admin
from django.core.cache import cache
from django.db import models
from django.utils.safestring import SafeText, mark_safe
from django.utils.timezone import now
from bd_models.models import Ball, Player, Special
from ballsdex.settings import settings 

class Achievement(models.Model):
    name = models.CharField(max_length=100, unique=True)
    achievement_emoji_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Discord emoji ID for achievement"
    )
    description = models.TextField()
    required_balls = models.ManyToManyField(
        Ball, 
        blank=True,
        related_name="achievements",
        help_text="Which countryballs you need to collect"
    ) 
    special_required = models.ManyToManyField(
        Special,
        blank=True,
        related_name="special_achievement",
        help_text="Any specials"
    )
    enable = models.BooleanField(
            help_text="on or off the achievements", default=True
    )
    reward = models.ManyToManyField(
        Ball,
        blank=True,
        related_name="achievements_reward", 
        help_text="rewards for achievements"
    ) 
    required_quantity = models.PositiveIntegerField(
        default=1,
        help_text="Number of each required combo (ball + special) needed"
    ) 
    self_catch = models.BooleanField(
             help_text="weither this achievement need self_catched balls", default=False
    ) 
    
    class Meta:
        db_table = "achievements"

    def __str__(self):
        return self.name

class PlayerAchievement(models.Model):
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="player_achievements"
    )
    achievement = models.ForeignKey(
        Achievement,
        on_delete=models.CASCADE,
        related_name="player_achievements"
    )
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "player_achievement"
        unique_together = ("player", "achievement")
        verbose_name = "Player Achievement"
        verbose_name_plural = "Player Achievements"

    def __str__(self):
        return f"{self.player} → {self.achievement}" 
        
