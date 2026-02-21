from discord.ui import Button, View
from discord.ext import commands
from discord import app_commands
from typing import TYPE_CHECKING
import discord
import random

from .models import AchievementRequiredBall, AchievementRequiredSpecial
from .models import PlayerAchievement
from .models import Achievement as AchievementModel
from .models import AchievementReward
from ballsdex.settings import settings
from .transformers import AchievementTransform, AchievementEnabledTransform
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.bot import BallsDexBot

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

from ballsdex.core.models import (
    Ball,
    BallInstance,
    BlacklistedGuild,
    BlacklistedID,
    GuildConfig,
    Player,
    Trade,
    TradeObject,
    balls,
    specials,
    Special
)

class Achievement(commands.GroupCog):
    """
    Achievement commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def list(self, interaction: discord.Interaction):
        """
        List all available achievements
        """
        await interaction.response.defer(ephemeral=True)

        achievement = await AchievementModel.filter(enable=True).all()
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        claimed_achievements = {
            pa.achievement_id
            for pa in await PlayerAchievement.filter(player=player)
        }

        if not achievement:
            await interaction.followup.send(
                "There are no achievements currently registered in the admin panel.",
                ephemeral=True
            )
            return

        entries = []

        for achievement in achievement:
            name = f"{achievement.name}"
            description = f"{achievement.description}"
            emote = self.bot.get_emoji(achievement.achievement_emoji_id) or ""

            if achievement.id in claimed_achievements:
                status = "✅"
            else:
                status = "❌"

            entry_lines = [f"Requirements: {description} {status}"]

            reward_ball_ids = await AchievementReward.filter(achievement_id=achievement.id).values_list('ball_id', flat=True)
            if reward_ball_ids:
                reward_balls = await Ball.filter(id__in=reward_ball_ids).all()
                reward_names = [ball.country for ball in reward_balls]
                entry_lines.append(f"Rewards: {', '.join(reward_names)}")
                            
            entry = (f"{emote} {name}", "\n".join(entry_lines))
            entries.append(entry)

        per_page = 10

        source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
        source.embed.description = f"__**{settings.bot_name} Achievements list**__"
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url
        )

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start()

    @app_commands.command()
    async def claim(
        self,
        interaction: discord.Interaction,
        achievement: AchievementEnabledTransform
    ):
        """
        Claim an achievement.
    
        Parameters
        ----------
        achievement: AchievementEnabledTransform
            The achievement you want to claim.
        """
        await interaction.response.defer(ephemeral=True)
        player, _ = await Player.get_or_create(discord_id=interaction.user.id) 
     
        if await PlayerAchievement.filter(player=player, achievement=achievement).exists():
            await interaction.followup.send( 
                f"You already claimed the achievement **{achievement.name}**!",
                ephemeral=True
            )
            return
    
        required_balls = await AchievementRequiredBall.filter(achievement_id=achievement.id).values_list('ball_id', flat=True)
        required_specials = await AchievementRequiredSpecial.filter(achievement_id=achievement.id).values_list("special_id", flat=True)
    
        player_qs = BallInstance.filter(player=player)
        if achievement.self_catch:
            player_qs = player_qs.filter(trade_player_id__isnull=True)
        player_instances = await player_qs.prefetch_related("ball", "special")
    
        if not required_balls and required_specials:
            special_count = sum(
                1 for bi in player_instances 
                if bi.special_id in required_specials
            )
            
            if special_count < achievement.required_quantity:
                specials = await Special.filter(id__in=required_specials).all()
                special_names = ", ".join(str(special) for special in specials)
                note = " (must be self-caught)" if achievement.self_catch else ""
                
                await interaction.followup.send(
                    f"❌ You need {achievement.required_quantity} of these specials{note}: {special_names}. You have {special_count}.",
                    ephemeral=True
                )
                return
    
        elif required_balls:
            if not required_specials:
                player_owned_ball_ids = {bi.ball_id for bi in player_instances}
                missing_balls = [
                    await Ball.get(id=ball_id)
                    for ball_id in required_balls
                    if ball_id not in player_owned_ball_ids
                ]
                
                if missing_balls:
                    note = " (must be self-caught)" if achievement.self_catch else ""
                    countries = ", ".join(ball.country for ball in missing_balls)
                    await interaction.followup.send(
                        f"❌ Missing required countryballs{note}: {countries}",
                        ephemeral=True
                    )
                    return
            
            else:
                missing_combinations = []
                
                for ball_id in required_balls:
                    has_ball_with_special = any(
                        bi.ball_id == ball_id and bi.special_id in required_specials
                        for bi in player_instances
                    )
                    
                    if not has_ball_with_special:
                        ball = await Ball.get(id=ball_id)
                        missing_combinations.append(ball.country)
                
                if missing_combinations:
                    specials = await Special.filter(id__in=required_specials).all()
                    special_names = ", ".join(str(s) for s in specials)
                    note = " (must be self-caught)" if achievement.self_catch else ""
                    
                    await interaction.followup.send(
                        f"❌ Missing required {note}: {', '.join(missing_combinations)} with any of [{special_names}]",
                        ephemeral=True
                    )
                    return
    
        try:
            reward_ball_ids = await AchievementReward.filter(achievement_id=achievement.id).values_list("ball_id", flat=True)
            
            for reward_ball_id in reward_ball_ids:
                await BallInstance.create(
                    player=player,
                    ball_id=reward_ball_id,  
                    special=None,
                    health_bonus=random.randint(-settings.max_health_bonus, settings.max_health_bonus),
                    attack_bonus=random.randint(-settings.max_attack_bonus, settings.max_attack_bonus),
                )
        except Exception as e:
            print(f"Error awarding rewards for achievement {achievement.id}: {e}")
    
        await PlayerAchievement.create(player=player, achievement=achievement)
        await interaction.followup.send(
            f"🎉 Congrats, you claimed **{achievement.name}**!",
            ephemeral=True
        )


