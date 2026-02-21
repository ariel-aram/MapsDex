import logging
import time
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Generic, Iterable, Optional, TypeVar

import discord
from discord import app_commands
from discord.interactions import Interaction
from tortoise.exceptions import DoesNotExist
from tortoise.expressions import Q, RawSQL
from tortoise.models import Model
from tortoise.timezone import now as tortoise_now
from ballsdex.core.bot import BallsDexBot 
from ballsdex.core.utils.transformers import ModelTransformer, ValidationError
from .models import Achievement 
from ballsdex.core.models import (
    Ball,
    BallInstance,
) 

from ballsdex.settings import settings 

class AchievementTransformer(ModelTransformer[Achievement]):
    name = "achievement"
    model = Achievement()

    def key(self, model: Achievement) -> str:
        return model.name.lower()

    async def load_items(self) -> Iterable[Achievement]:
        return await Achievement.all()
        
    async def get_options(
        self, interaction: discord.Interaction["BallsDexBot"], value: str
     ) -> list[app_commands.Choice[str]]:
          items = await self.load_items()
          return [      
               app_commands.Choice(name =a.name, value=a. name)
               for a in items
               if value.lower() in a.name.lower()
           ][:25]  
             
    async def transform(
               self, interaction: discord.Interaction["BallsDexBot"], value: str
           ) -> Optional[Achievement]:
               for a in await self.load_items():
                   if a.name.lower() == value.lower():
                       return a
               await interaction.response.send_message(
                   "The achievement could not be found. Please use the autocompletion.",
                   ephemeral=True,
               )
               return None       

class AchievementEnabledTransformer(AchievementTransformer):
    async def load_items(self) -> Iterable[Achievement]:
        achievements = await Achievement.all()
        return {a.name: a for a in achievements if a.enable}.values()

    async def get_options(
        self, interaction: discord.Interaction["BallsDexBot"], value: str
    ) -> list[app_commands.Choice[str]]:
        items = await self.load_items()
        return [
            app_commands.Choice(name=a.name, value=a.name)
            for a in items
            if value.lower() in a.name.lower()
        ][:25]

    async def transform(
       self, interaction: discord.Interaction["BallsDexBot"], value: str
   ) -> Optional[Achievement]:
       achievement = next(
           (a for a in await self.load_items() if a.name.lower() == value.lower()), None
       )
       if not achievement:
           await interaction.response.send_message(
               "This achievement does not exist or isn't enabled. Use autocomplete to pick a valid one.",
               ephemeral=True,
           )
           return None
       return achievement
            
AchievementTransform = app_commands.Transform[Achievement, AchievementTransformer] 
AchievementEnabledTransform = app_commands.Transform[Achievement, AchievementEnabledTransformer]
