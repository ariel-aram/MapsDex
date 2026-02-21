import asyncio
import logging
import random
import re
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.utils import format_dt
from tortoise.exceptions import BaseORMException, DoesNotExist

from ballsdex.core.bot import BallsDexBot
from ballsdex.core.models import (
    Ball,
    BallInstance,
    BlacklistedGuild,
    BlacklistHistory,
    GuildConfig,
    Player,
    Special,
    Trade,
    TradeObject,
    balls,
)
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.logging import log_action
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.utils.transformers import (
    BallTransform,
    EconomyTransform,
    RegimeTransform,
    SpecialTransform,
)
from ballsdex.settings import settings
from .logging_decorator import log_admin_command

if TYPE_CHECKING:
    from ballsdex.packages.countryballs.cog import CountryBallsSpawner
    from ballsdex.packages.countryballs.countryball import BallSpawnView

log = logging.getLogger("ballsdex.packages.admin.balls")
FILENAME_RE = re.compile(r"^(.+)(\.\S+)$")


async def save_file(attachment: discord.Attachment) -> Path:
    path = Path(f"./admin_panel/media/{attachment.filename}")
    match = FILENAME_RE.match(attachment.filename)
    if not match:
        raise TypeError("The file you uploaded lacks an extension.")
    i = 1
    while path.exists():
        path = Path(f"./admin_panel/media/{match.group(1)}-{i}{match.group(2)}")
        i = i + 1
    await attachment.save(path)
    return path.relative_to("./admin_panel/media/")


class Balls(app_commands.Group):
    """
    Countryballs management
    """

    async def _spawn_bomb(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball_cls: type["BallSpawnView"],
        countryball: Ball | None,
        channel: discord.TextChannel,
        n: int,
        special: Special | None = None,
        atk_bonus: int | None = None,
        hp_bonus: int | None = None,
        tier_range: tuple[int, int] | None = None,
    ):
        spawned = 0

        # Build tier-filtered collectibles list if tier_range is specified
        filtered_balls = None
        if tier_range and not countryball:
            min_tier, max_tier = tier_range
            enabled_collectibles = [x for x in balls.values() if x.enabled]

            # Build rarity to collectibles mapping
            rarity_to_collectibles = {}
            for c in enabled_collectibles:
                rarity_to_collectibles.setdefault(c.rarity, []).append(c)

            sorted_rarities = sorted(rarity_to_collectibles.keys())

            # Validate tier range
            if min_tier < 1 or max_tier > len(sorted_rarities) or min_tier > max_tier:
                await interaction.response.send_message(
                    f"Invalid tier range. Must be between T1-T{len(sorted_rarities)} and min <= max.",
                    ephemeral=True
                )
                return

            # Filter balls within the tier range
            filtered_balls = []
            for tier_idx in range(min_tier - 1, max_tier):
                rarity = sorted_rarities[tier_idx]
                filtered_balls.extend(rarity_to_collectibles[rarity])

        async def update_message_loop():
            for i in range(5 * 12 * 10):  # timeout progress after 10 minutes
                range_text = f" (T{tier_range[0]}-T{tier_range[1]})" if tier_range else ""
                await interaction.followup.edit_message(
                    "@original",  # type: ignore
                    content=f"Spawn bomb in progress in {channel.mention}, "
                    f"{settings.collectible_name.title()}: {countryball or f'Random{range_text}'}\n"
                    f"{spawned}/{n} spawned ({round((spawned / n) * 100)}%)",
                )
                await asyncio.sleep(5)
            await interaction.followup.edit_message(
                "@original", content="Spawn bomb seems to have timed out."  # type: ignore
            )

        await interaction.response.send_message(
            f"Starting spawn bomb in {channel.mention}...", ephemeral=True
        )
        task = interaction.client.loop.create_task(update_message_loop())
        try:
            for i in range(n):
                if not countryball:
                    if filtered_balls is not None:
                        # Pick random from filtered list
                        if not filtered_balls:
                            task.cancel()
                            await interaction.followup.edit_message(
                                "@original",  # type: ignore
                                content=f"No {settings.plural_collectible_name} found in the specified tier range.",
                            )
                            return
                        ball_model = random.choice(filtered_balls)
                        ball = countryball_cls(interaction.client, ball_model)
                    else:
                        ball = await countryball_cls.get_random(interaction.client)
                else:
                    ball = countryball_cls(interaction.client, countryball)
                ball.special = special
                ball.atk_bonus = atk_bonus
                ball.hp_bonus = hp_bonus
                result = await ball.spawn(channel)
                if not result:
                    task.cancel()
                    await interaction.followup.edit_message(
                        "@original",  # type: ignore
                        content=f"A {settings.collectible_name} failed to spawn, probably "
                        "indicating a lack of permissions to send messages "
                        f"or upload files in {channel.mention}.",
                    )
                    return
                spawned += 1
            task.cancel()
            await interaction.followup.send(
                f"Successfully spawned {spawned} {settings.plural_collectible_name} "
                f"in {channel.mention}!",
            )
            return {"summary_message": f"Spawn bomb completed: {spawned} {settings.plural_collectible_name} spawned in {channel.mention}"}
        finally:
            task.cancel()

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.checks.cooldown(1, 100, key=lambda i: i.user.id)
    @log_admin_command(log_summary=True)
    async def spawn(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball: BallTransform | None = None,
        channel: discord.TextChannel | None = None,
        n: app_commands.Range[int, 1, 100] = 1,
        special: SpecialTransform | None = None,
        atk_bonus: int | None = None,
        hp_bonus: int | None = None,
        tier_range: str | None = None,
    ):
        """
        Force spawn a random or specified countryball.

        Parameters
        ----------
        countryball: Ball | None
            The countryball you want to spawn. Random according to rarities if not specified.
        channel: discord.TextChannel | None
            The channel you want to spawn the countryball in. Current channel if not specified.
        n: int
            The number of countryballs to spawn. If no countryball was specified, it's random
            every time.
        special: Special | None
            Force the countryball to have a special attribute when caught.
        atk_bonus: int | None
            Force the countryball to have a specific attack bonus when caught.
        hp_bonus: int | None
            Force the countryball to have a specific health bonus when caught.
        tier_range: str | None
            Tier range to spawn from (e.g. "2-8" for T2 to T8). Only works with random spawns.
        """
        # the transformer triggered a response, meaning user tried an incorrect input
        if interaction.response.is_done():
            return

        # Parse and validate tier_range parameter
        parsed_range = None
        if tier_range:
            if countryball:
                await interaction.response.send_message(
                    "The `tier_range` parameter can only be used with random spawns, not with a specific countryball.",
                    ephemeral=True
                )
                return

            # Parse tier_range format "min-max"
            if "-" not in tier_range:
                await interaction.response.send_message(
                    "Invalid tier_range format. Use format like '2-8' for T2 to T8.",
                    ephemeral=True
                )
                return

            try:
                parts = tier_range.split("-")
                if len(parts) != 2:
                    raise ValueError
                min_tier = int(parts[0])
                max_tier = int(parts[1])
                parsed_range = (min_tier, max_tier)
            except ValueError:
                await interaction.response.send_message(
                    "Invalid tier_range format. Use format like '2-8' for T2 to T8.",
                    ephemeral=True
                )
                return

        cog = cast("CountryBallsSpawner | None", interaction.client.get_cog("CountryBallsSpawner"))
        if not cog:
            prefix = (
                settings.prefix
                if interaction.client.intents.message_content or not interaction.client.user
                else f"{interaction.client.user.mention} "
            )
            # do not replace `countryballs` with `settings.collectible_name`, it is intended
            await interaction.response.send_message(
                "The `countryballs` package is not loaded, this command is unavailable.\n"
                "Please resolve the errors preventing this package from loading. Use "
                f'"{prefix}reload countryballs" to try reloading it.',
                ephemeral=True,
            )
            return

        special_attrs = []
        if special is not None:
            special_attrs.append(f"special={special.name}")
        if atk_bonus is not None:
            special_attrs.append(f"atk={atk_bonus}")
        if hp_bonus is not None:
            special_attrs.append(f"hp={hp_bonus}")
        if n > 1:
            await self._spawn_bomb(
                interaction,
                cog.countryball_cls,
                countryball,
                channel or interaction.channel,  # type: ignore
                n,
                special,
                atk_bonus,
                hp_bonus,
                parsed_range,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        if not countryball:
            if parsed_range:
                # Handle single spawn with tier range
                min_tier, max_tier = parsed_range
                enabled_collectibles = [x for x in balls.values() if x.enabled]

                # Build rarity to collectibles mapping
                rarity_to_collectibles = {}
                for c in enabled_collectibles:
                    rarity_to_collectibles.setdefault(c.rarity, []).append(c)

                sorted_rarities = sorted(rarity_to_collectibles.keys())

                # Validate tier range
                if min_tier < 1 or max_tier > len(sorted_rarities) or min_tier > max_tier:
                    await interaction.followup.send(
                        f"Invalid tier range. Must be between T1-T{len(sorted_rarities)} and min <= max.",
                        ephemeral=True
                    )
                    return

                # Filter balls within the tier range
                filtered_balls = []
                for tier_idx in range(min_tier - 1, max_tier):
                    rarity = sorted_rarities[tier_idx]
                    filtered_balls.extend(rarity_to_collectibles[rarity])

                # Check if any balls were found in the tier range
                if not filtered_balls:
                    await interaction.followup.send(
                        f"No {settings.plural_collectible_name} found in tier range T{min_tier}-T{max_tier}.",
                        ephemeral=True
                    )
                    return

                # Pick random from filtered list
                ball_model = random.choice(filtered_balls)
                ball = cog.countryball_cls(interaction.client, ball_model)
            else:
                ball = await cog.countryball_cls.get_random(interaction.client)
        else:
            ball = cog.countryball_cls(interaction.client, countryball)
        ball.special = special
        ball.atk_bonus = atk_bonus
        ball.hp_bonus = hp_bonus
        result = await ball.spawn(channel or interaction.channel)  # type: ignore

        if result:
            await interaction.followup.send(
                f"{settings.collectible_name.title()} spawned.", ephemeral=True
            )
            return {"summary_message": f"{settings.collectible_name.title()} {ball.name} spawned in {channel or interaction.channel.mention}"}

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command(log_summary=True)
    async def give(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball: BallTransform,
        user: discord.User,
        amount: app_commands.Range[int, 1, 100] = 1,
        special: SpecialTransform | None = None,
        health_bonus: int | None = None,
        attack_bonus: int | None = None,
    ):
        """
        Give the specified countryball to a player.

        Parameters
        ----------
        countryball: Ball
        user: discord.User
        amount: int | None
        special: Special | None
        health_bonus: int | None
            Omit this to make it random.
        attack_bonus: int | None
            Omit this to make it random.
        """

        # the transformers triggered a response, meaning user tried an incorrect input
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        player, created = await Player.get_or_create(discord_id=user.id)
        instances = []
        for _ in range(amount):
            instance = await BallInstance.create(
                ball=countryball,
                player=player,
                attack_bonus=(
                    attack_bonus
                    if attack_bonus is not None
                    else random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
                ),
                health_bonus=(
                    health_bonus
                    if health_bonus is not None
                    else random.randint(-settings.max_health_bonus, settings.max_health_bonus)
                ),
                special=special,
            )
            instances.append(instance)

        if amount == 1:
            await interaction.followup.send(
                (
                    f"`{countryball.country}` `({instances[0].pk:0X})` "
                    f"{settings.collectible_name} was successfully given to `{user}`.\n"
                    f"Special: `{special.name if special else None}` • ATK: "
                    f"`{instances[0].attack_bonus:+d}` • HP:`{instances[0].health_bonus:+d}` "
                )
            )
            await log_action(
                f"{interaction.user} gave {settings.collectible_name} "
                f"{countryball.country} `({instances[0].pk:0X})` to {user}. "
                f"(Special={special.name if special else None} "
                f"ATK={instances[0].attack_bonus:+d} HP={instances[0].health_bonus:+d}).",
                interaction.client,
            )
            return {"summary_message": f"Gave 1 {countryball.country} to {user}"}
        else:
            followup_header = (
                f"`{countryball.country}` {settings.plural_collectible_name} were successfully given to "
                f"`{user}` ({amount} total):"
            )
            followup_lines = [
                f"{i+1}. (Special: `{special.name if special else None}`, ATK: `{inst.attack_bonus:+d}`, HP: `{inst.health_bonus:+d}`, ID: `{inst.pk:0X}`)"
                for i, inst in enumerate(instances)
            ]
            await interaction.followup.send(followup_header + "\n" + "\n".join(followup_lines))
            
            return {"summary_message": f"Gave {amount} {countryball.country}{'\'s' if amount > 1 else ''} to {user} ({amount} total)"}

    @app_commands.command(name="info")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_info(self, interaction: discord.Interaction[BallsDexBot], countryball_id: str):
        """
        Show information about a countryball.

        Parameters
        ----------
        countryball_id: str
            The ID of the countryball you want to get information about.
        """
        try:
            pk = int(countryball_id, 16)
        except ValueError:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave is not valid.", ephemeral=True
            )
            return
        try:
            ball = await BallInstance.get(id=pk).prefetch_related(
                "player", "trade_player", "special"
            )
        except DoesNotExist:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave does not exist.", ephemeral=True
            )
            return
        spawned_time = format_dt(ball.spawned_time, style="R") if ball.spawned_time else "N/A"
        catch_time = (
            (ball.catch_date - ball.spawned_time).total_seconds()
            if ball.catch_date and ball.spawned_time
            else "N/A"
        )
        admin_url = (
            f"[View online](<{settings.admin_url}/bd_models/ballinstance/{ball.pk}/change/>)"
            if settings.admin_url
            else ""
        )
        await interaction.response.send_message(
            f"**{settings.collectible_name.title()} ID:** {ball.pk}\n"
            f"**Player:** {ball.player}\n"
            f"**Name:** {ball.countryball}\n"
            f"**Attack:** {ball.attack}\n"
            f"**Attack bonus:** {ball.attack_bonus}\n"
            f"**Health bonus:** {ball.health_bonus}\n"
            f"**Health:** {ball.health}\n"
            f"**Special:** {ball.special.name if ball.special else None}\n"
            f"**Caught at:** {format_dt(ball.catch_date, style='R')}\n"
            f"**Spawned at:** {spawned_time}\n"
            f"**Catch time:** {catch_time} seconds\n"
            f"**Caught in:** {ball.server_id if ball.server_id else 'N/A'}\n"
            f"**Traded:** {ball.trade_player}\n{admin_url}",
            ephemeral=True,
        )

    @app_commands.command(name="delete")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_delete(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball_id: str,
        soft_delete: bool = True,
    ):
        """
        Delete a countryball.

        Parameters
        ----------
        countryball_id: str
            The ID of the countryball you want to delete.
        soft_delete: bool
            Whether the countryball should be kept in database or fully wiped.
        """
        try:
            ballIdConverted = int(countryball_id, 16)
        except ValueError:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave is not valid.", ephemeral=True
            )
            return
        try:
            ball = await BallInstance.get(id=ballIdConverted)
        except DoesNotExist:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave does not exist.", ephemeral=True
            )
            return
        if soft_delete:
            ball.deleted = True
            await ball.save()
            await interaction.response.send_message(
                f"{settings.collectible_name.title()} {countryball_id} soft deleted.",
                ephemeral=True,
            )
            return {"summary_message": f"Soft deleted {settings.collectible_name} {countryball_id} ({ball.countryball})"}
        else:
            await ball.delete()
            await interaction.response.send_message(
                f"{settings.collectible_name.title()} {countryball_id} hard deleted.",
                ephemeral=True,
            )
            return {"summary_message": f"Hard deleted {settings.collectible_name} {countryball_id} ({ball.countryball})"}

    @app_commands.command(name="transfer")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_transfer(
        self,
        interaction: discord.Interaction[BallsDexBot],
        user_1: discord.User,
        user_2: discord.User | None = None,
        countryball_id: str | None = None,
        percentage: int | None = None,
        users: str | None = None,
        special: SpecialTransform | None = None,
        clean_balls: bool = False,
        trade_player_id: int | None = None,
    ):
        """
        Transfer countryballs between users.

        Parameters
        ----------
        user_1: discord.User
            When using countryball_id: The user to transfer the countryball to (recipient).
            When using percentage: The user to transfer countryballs from (source).
        user_2: discord.User | None
            The user to transfer countryballs to. Required when using percentage without users.
        countryball_id: str | None
            The ID(s) of the countryball you want to transfer. Can be comma-separated (e.g., "ABC, DEF, 123"). Cannot be used with percentage.
        percentage: int | None
            The percentage of countryballs to transfer. Can be combined with users for pool distribution.
        users: str | None
            Comma-separated user IDs for pool distribution (e.g., "123, 456, 789").
            Splits balls evenly among all users. Useful for giveaways.
        special: Special | None
            Filter by special when using percentage transfer.
        clean_balls: bool
            If True, removes trade player, spawn date, catch time, trade history, and server ID.
        trade_player_id: int | None
            Discord user ID to set as the trade player for transferred balls. Only works with clean_balls=True.
        """
        # Validation
        if countryball_id and (percentage or users):
            await interaction.response.send_message(
                "Cannot use countryball_id with percentage or users.", ephemeral=True
            )
            return

        if not countryball_id and not percentage:
            await interaction.response.send_message(
                "Either countryball_id or percentage must be provided.", ephemeral=True
            )
            return

        if percentage and not (1 <= percentage <= 100):
            await interaction.response.send_message(
                "Percentage must be between 1 and 100.", ephemeral=True
            )
            return

        if trade_player_id and not clean_balls:
            await interaction.response.send_message(
                "trade_player_id can only be used when clean_balls is True.", ephemeral=True
            )
            return

        # Parse pool users
        pool_user_ids = []
        if users:
            try:
                pool_user_ids = [int(uid.strip()) for uid in users.split(",")]
                if len(pool_user_ids) < 2:
                    raise ValueError("Need at least 2 users")
            except ValueError:
                await interaction.response.send_message(
                    "Invalid users format. Use comma-separated IDs (e.g., '123, 456, 789') with at least 2 users.",
                    ephemeral=True,
                )
                return

        if percentage and not users and not user_2:
            await interaction.response.send_message(
                "user_2 required when using percentage without users.", ephemeral=True
            )
            return

        if percentage:
            player_1 = await Player.get_or_none(discord_id=user_1.id)
            if not player_1:
                await interaction.response.send_message(
                    f"{user_1} does not exist or has no {settings.plural_collectible_name}.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)

            # Filter by special if specified
            filters = {"player": player_1, "deleted": False}
            if special:
                filters["special"] = special

            balls = await BallInstance.filter(**filters)
            if not balls:
                special_text = f" with special {special.name}" if special else ""
                await interaction.followup.send(
                    f"{user_1} has no {settings.plural_collectible_name}{special_text} to transfer.", ephemeral=True
                )
                return

            to_transfer = balls if percentage == 100 else random.sample(balls, int(len(balls) * (percentage / 100)))
            if not to_transfer:
                await interaction.followup.send(
                    f"Percentage results in 0 {settings.plural_collectible_name} to transfer.", ephemeral=True
                )
                return

            # Setup rotation
            if users:
                new_players = [(await Player.get_or_create(discord_id=uid))[0] for uid in pool_user_ids]
            else:
                player_2, _ = await Player.get_or_create(discord_id=user_2.id)
                new_players = [player_2]

            rot = cycle(new_players)

            # Transfer balls
            for ball in to_transfer:
                if clean_balls:
                    await TradeObject.filter(ballinstance_id=ball.id).delete()
                    ball.trade_player_id = trade_player_id
                    ball.spawned_time = None
                    ball.catch_date = datetime.now()
                    ball.server_id = None
                    ball.favorite = False

                ball.player = next(rot)
                if users:
                    ball.favorite = False
                await ball.save()

            # Response
            count = len(to_transfer)
            special_text = f" {special.name}" if special else ""
            target = f"pool of {len(pool_user_ids)} users" if users else str(user_2)
            await interaction.followup.send(
                f"Transferred {count}{special_text} {settings.plural_collectible_name} ({percentage}%) from {user_1} to {target}.",
                ephemeral=True,
            )
            target = f"pool: {', '.join(str(uid) for uid in pool_user_ids)}" if users else str(user_2)
            return {"summary_message": f"Transferred {count}{special_text} {settings.plural_collectible_name} ({percentage}%) from {user_1} to {target}"}
        else:
            # Parse comma-separated IDs
            ball_ids_str = [bid.strip() for bid in countryball_id.split(",")]
            ball_ids = []

            try:
                for bid in ball_ids_str:
                    ball_ids.append(int(bid, 16))
            except ValueError:
                await interaction.response.send_message(
                    f"Invalid {settings.collectible_name} ID format. Use hex IDs separated by commas.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)

            # Fetch all balls
            balls_to_transfer = []
            for bid in ball_ids:
                try:
                    ball = await BallInstance.get(id=bid).prefetch_related("player")
                    if ball.deleted:
                        await interaction.followup.send(
                            f"The {settings.collectible_name} {hex(bid)[2:].upper()} is deleted and cannot be transferred.",
                            ephemeral=True,
                        )
                        return
                    balls_to_transfer.append(ball)
                except DoesNotExist:
                    await interaction.followup.send(
                        f"The {settings.collectible_name} ID {hex(bid)[2:].upper()} does not exist.", ephemeral=True
                    )
                    return

            # Setup recipients
            if users:
                new_players = [(await Player.get_or_create(discord_id=uid))[0] for uid in pool_user_ids]
            else:
                recipient, _ = await Player.get_or_create(discord_id=user_1.id)
                new_players = [recipient]

            rot = cycle(new_players)

            # Transfer all balls
            transferred = []
            for ball in balls_to_transfer:
                original_player = ball.player
                new_player = next(rot)

                if original_player.discord_id == new_player.discord_id:
                    continue  # Skip if already belongs to recipient

                if clean_balls:
                    await TradeObject.filter(ballinstance_id=ball.id).delete()
                    ball.trade_player_id = trade_player_id
                    ball.spawned_time = None
                    ball.catch_date = datetime.now()
                    ball.server_id = None
                    ball.favorite = False

                ball.player = new_player
                if users:
                    ball.favorite = False
                await ball.save()
                transferred.append((ball, original_player, new_player))

            if not transferred:
                target = f"pool of {len(pool_user_ids)} users" if users else str(user_1)
                await interaction.followup.send(
                    f"All specified {settings.plural_collectible_name} already belong to {target}.",
                    ephemeral=True,
                )
                return

            # Response
            count = len(transferred)
            target = f"pool of {len(pool_user_ids)} users" if users else str(user_1)

            if count == 1 and not users:
                ball, original_player, new_player = transferred[0]
                await interaction.followup.send(
                    f"Transferred {ball}({ball.pk}) from {original_player} to {new_player}.",
                    ephemeral=True,
                )
                return {"summary_message": f"Transferred {ball}({ball.pk}) from {original_player} to {new_player}"}
            else:
                ball_list = ", ".join([f"{hex(b.pk)[2:].upper()}" for b, _, _ in transferred])
                await interaction.followup.send(
                    f"Transferred {count} {settings.plural_collectible_name} to {target}.",
                    ephemeral=True,
                )
                target = f"pool: {', '.join(str(uid) for uid in pool_user_ids)}" if users else str(user_1)
                return {"summary_message": f"Transferred {count} {settings.plural_collectible_name} to {target}"}

    @app_commands.command(name="reset")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_reset(
        self,
        interaction: discord.Interaction[BallsDexBot],
        user: discord.User,
        percentage: int | None = None,
        soft_delete: bool = True,
    ):
        """
        Reset a player's countryballs.

        Parameters
        ----------
        user: discord.User
            The user you want to reset the countryballs of.
        percentage: int | None
            The percentage of countryballs to delete, if not all. Used for sanctions.
        soft_delete: bool
            If true, the countryballs will be marked as deleted instead of being removed from the
            database.
        """
        player = await Player.get_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(
                "The user you gave does not exist.", ephemeral=True
            )
            return
        if percentage and not 0 < percentage < 100:
            await interaction.response.send_message(
                "The percentage must be between 1 and 99.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        method = "soft" if soft_delete else "hard"
        if not percentage:
            text = (
                f"Are you sure you want to {method} delete {user}'s "
                f"{settings.plural_collectible_name}?"
            )
        else:
            text = (
                f"Are you sure you want to {method} delete {percentage}% of "
                f"{user}'s {settings.plural_collectible_name}?"
            )
        view = ConfirmChoiceView(
            interaction,
            accept_message=f"Confirmed, {method} deleting the "
            f"{settings.plural_collectible_name}...",
            cancel_message="Request cancelled.",
        )
        await interaction.followup.send(
            text,
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return
        if percentage:
            balls = await BallInstance.filter(player=player)
            to_delete = random.sample(balls, int(len(balls) * (percentage / 100)))
            for ball in to_delete:
                if soft_delete:
                    ball.deleted = True
                    await ball.save()
                else:
                    await ball.delete()
            count = len(to_delete)
        else:
            if soft_delete:
                count = await BallInstance.filter(player=player).update(deleted=True)
            else:
                count = await BallInstance.filter(player=player).delete()
        await interaction.followup.send(
            f"{count} {settings.plural_collectible_name} from {user} have been {method} deleted.",
            ephemeral=True,
        )
        return {"summary_message": f"Reset {user}'s collection: {count} {settings.plural_collectible_name} {method} deleted"}

    @app_commands.command(name="count")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_count(
        self,
        interaction: discord.Interaction[BallsDexBot],
        user: discord.User | None = None,
        countryball: BallTransform | None = None,
        special: SpecialTransform | None = None,
        deleted: bool = False,
    ):
        """
        Count the number of countryballs that a player has or how many exist in total.

        Parameters
        ----------
        user: discord.User
            The user you want to count the countryballs of.
        countryball: Ball
        special: Special
        deleted: bool
            Include soft deleted countryballs
        """
        if interaction.response.is_done():
            return
        filters = {}
        if countryball:
            filters["ball"] = countryball
        if special:
            filters["special"] = special
        if user:
            filters["player__discord_id"] = user.id
        await interaction.response.defer(ephemeral=True, thinking=True)
        qs = BallInstance.all_objects if deleted else BallInstance
        balls = await qs.filter(**filters).count()
        verb = "is" if balls == 1 else "are"
        country = f"{countryball.country} " if countryball else ""
        plural = "s" if balls > 1 or balls == 0 else ""
        special_str = f"{special.name} " if special else ""
        if user:
            await interaction.followup.send(
                f"{user} has {balls} {special_str}"
                f"{country}{settings.collectible_name}{plural}."
            )
            return {"summary_message": f"Counted {balls} {special_str}{country}{settings.collectible_name}{plural} for {user}"}
        else:
            await interaction.followup.send(
                f"There {verb} {balls} {special_str}"
                f"{country}{settings.collectible_name}{plural}."
            )
            return {"summary_message": f"Counted {balls} {special_str}{country}{settings.collectible_name}{plural} in total"}

    @app_commands.command(name="create")
    @app_commands.checks.has_any_role(*settings.admin_role_ids, *settings.root_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_create(
        self,
        interaction: discord.Interaction[BallsDexBot],
        *,
        name: app_commands.Range[str, None, 48],
        regime: RegimeTransform,
        economy: EconomyTransform,
        health: int,
        attack: int,
        emoji_id: app_commands.Range[str, 17, 21],
        capacity_name: app_commands.Range[str, None, 64],
        capacity_description: app_commands.Range[str, None, 256],
        wild_card: discord.Attachment,
        collection_card: discord.Attachment,
        image_credits: str,
        rarity: float | None = None,
        enabled: bool = False,
        tradeable: bool = False,
    ):
        """
        Admin command for creating countryballs. They are disabled by default.

        Parameters
        ----------
        name: str
            Name to be used for this countryball
        regime: Regime
            Regime to be used for this countryball
        economy: Economy
            Economy to be used for this countryball
        health: int
            Health to be used for this countryball
        attack: int
            Attack to be used for this countryball
        emoji_id: str
            An emoji ID, the bot will check if it can access the custom emote
        capacity_name: str
            Title of the countryball capacity
        capacity_description: str
            Description of the countryball capacity
        wild_card: discord.Attachment
            Artwork used when a countryball spawns
        collection_card: discord.Attachment
            Artwork used when viewing a countryball
        image_credits: str
            Author of the artwork
        rarity: float | None
            Custom rarity value. If not provided, rarity will be calculated automatically
        enabled: bool
            If true, the countryball can spawn and will show up in global completion
        tradeable: bool
            If false, all instances are untradeable
        """
        if regime is None or interaction.response.is_done():  # regime autocomplete failed
            return

        if economy is None or interaction.response.is_done():  # economy autocomplete failed
            return

        if not emoji_id.isnumeric():
            await interaction.response.send_message(
                "`emoji_id` is not a valid number.", ephemeral=True
            )
            return
        emoji = interaction.client.get_emoji(int(emoji_id))
        if not emoji:
            await interaction.response.send_message(
                "The bot does not have access to the given emoji.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            wild_card_path = await save_file(wild_card)
        except Exception as e:
            log.exception("Failed saving file when creating countryball", exc_info=True)
            await interaction.followup.send(
                f"Failed saving the attached file: {collection_card.url}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
            return
        try:
            collection_card_path = await save_file(collection_card)
        except Exception as e:
            log.exception("Failed saving file when creating countryball", exc_info=True)
            await interaction.followup.send(
                f"Failed saving the attached file: {collection_card.url}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
            return

        if rarity is None:
            rarity = max(round(10 * (1 - ((health / 5000 + attack / 5000) / 2)), 1), 0.1)

        try:
            ball = await Ball.create(
                country=name,
                regime=regime,
                economy=economy,
                health=health,
                attack=attack,
                rarity=rarity,
                enabled=enabled,
                tradeable=tradeable,
                emoji_id=emoji_id,
                wild_card=str(wild_card_path),
                collection_card=str(collection_card_path),
                credits=image_credits,
                capacity_name=capacity_name,
                capacity_description=capacity_description,
            )
        except BaseORMException as e:
            log.exception("Failed creating countryball with admin command", exc_info=True)
            await interaction.followup.send(
                f"Failed creating the {settings.collectible_name}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
        else:
            files = [await wild_card.to_file(), await collection_card.to_file()]
            await interaction.client.load_cache()
            admin_url = (
                f"[View online](<{settings.admin_url}/bd_models/ball/{ball.pk}/change/>)\n"
                if settings.admin_url
                else ""
            )
            rarity_note = " (custom)" if rarity else " (calculated)"
            await interaction.followup.send(
                f"Successfully created a {settings.collectible_name} with ID {ball.pk}! "
                f"The internal cache was reloaded.\n{admin_url}"
                "\n"
                f"{name=} regime={regime.name} economy={economy.name} "
                f"{health=} {attack=} rarity={ball.rarity}{rarity_note} {enabled=} {tradeable=} emoji={emoji}",
                files=files,
            )
            return {"summary_message": f"Created new {settings.collectible_name}: {name} (ID: {ball.pk})"}

    @app_commands.command(name="farms")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @log_admin_command(log_summary=True)
    async def balls_farms(
        self,
        interaction: discord.Interaction[BallsDexBot],
        action: bool = False,
    ):
        """
        Detect farm servers and optionally take action against them.

        Parameters
        ----------
        action: bool
            If True, transfer balls to escrow and blacklist servers. If False, only show details.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Get escrow player
        ESCROW = await Player.get_or_none(discord_id=1406796704807915630)
        if not ESCROW:
            await interaction.followup.send(
                "Error: Escrow account not found.", ephemeral=True
            )
            return

        moderator_id = interaction.user.id
        reason = f"Server under 15 members.\nBy: {interaction.user} ({moderator_id})"

        # Find qualifying guilds
        qualifying_guilds = []

        for guild in interaction.client.guilds:
            if guild.member_count < 15:
                config = await GuildConfig.get_or_none(guild_id=guild.id)
                if config and config.enabled and config.guild_id:
                    # Check if not already blacklisted
                    is_blacklisted = await BlacklistedGuild.get_or_none(discord_id=guild.id)
                    if not is_blacklisted:
                        # Count balls caught in this server
                        ball_count = await BallInstance.filter(server_id=guild.id).count()

                        # Get guild owner
                        try:
                            owner = await interaction.client.fetch_user(guild.owner_id)
                            owner_name = f"{owner} ({owner.id})"
                        except:
                            owner_name = f"Unknown ({guild.owner_id})"

                        qualifying_guilds.append({
                            "id": guild.id,
                            "name": guild.name,
                            "owner": owner_name,
                            "member_count": guild.member_count,
                            "ball_count": ball_count,
                        })

        if not qualifying_guilds:
            await interaction.followup.send("No farm servers found.", ephemeral=True)
            return

        # If action is False, just display the servers
        if not action:
            # Group servers by owner and count
            owner_server_counts: dict[int, list[dict]] = {}
            for guild_data in qualifying_guilds:
                # Extract owner ID from the owner string
                owner_id_str = guild_data['owner'].split('(')[-1].rstrip(')')
                try:
                    owner_id = int(owner_id_str)
                    if owner_id not in owner_server_counts:
                        owner_server_counts[owner_id] = []
                    owner_server_counts[owner_id].append(guild_data)
                except:
                    pass
            
            # Create entries with formatted server names
            entries: list[tuple[str, str]] = []
            for guild_data in qualifying_guilds:
                field_name = f"`{guild_data['id']}`"
                
                # Extract owner ID and format as "Server X"
                owner_id_str = guild_data['owner'].split('(')[-1].rstrip(')')
                try:
                    owner_id = int(owner_id_str)
                    # Always format as "Server X", even for single servers
                    if owner_id in owner_server_counts:
                        server_num = owner_server_counts[owner_id].index(guild_data) + 1
                        display_name = f"Server {server_num}"
                    else:
                        display_name = "Unknown Server"
                except:
                    display_name = "Unknown Server"
                
                field_value = (
                    f"**{display_name}**\n"
                    f"Owner: {guild_data['owner']}\n"
                    f"Members: {guild_data['member_count']}\n"
                    f"Maps Caught: {guild_data['ball_count']}"
                )
                entries.append((field_name, field_value))

            source = FieldPageSource(entries, per_page=10, inline=False)
            source.embed.title = f"Detected {len(qualifying_guilds)} Farm Server(s)"
            source.embed.color = discord.Color.orange()
            source.embed.set_footer(
                text="Use /admin maps farms action:True to take action against these servers"
            )

            pages = Pages(source=source, interaction=interaction, compact=True)
            await pages.start(ephemeral=True)
            return

        # If action is True, transfer balls and blacklist
        total_transferred = 0

        for guild_data in qualifying_guilds:
            gid = guild_data["id"]
            guild_name = guild_data["name"]

            # Transfer all balls from this server to escrow
            async for ball in BallInstance.filter(server_id=gid):
                # Delete any trade objects referencing this ball
                await TradeObject.filter(ballinstance_id=ball.id).delete()

                # Transfer ball to escrow
                ball.player = ESCROW
                ball.favorite = False
                ball.server_id = None
                ball.trade_player = ESCROW
                ball.spawned_time = None
                ball.catch_date = datetime.now()
                await ball.save()

                total_transferred += 1

            # Blacklist the guild
            await BlacklistedGuild.create(
                discord_id=gid, reason=reason, moderator_id=moderator_id
            )

            await BlacklistHistory.create(
                discord_id=gid,
                reason=reason,
                moderator_id=moderator_id,
                id_type="guild",
            )

            # Add to bot's blacklist cache
            interaction.client.blacklist_guild.add(gid)

        # Send summary
        summary = (
            f"**Farm Detection Complete**\n\n"
            f"{'Server' if len(qualifying_guilds) == 1 else 'Servers'} Blacklisted: {len(qualifying_guilds)}\n"
            f"Total {settings.collectible_name if total_transferred == 1 else settings.plural_collectible_name} Transferred: {total_transferred}"
        )

        await interaction.followup.send(summary, ephemeral=True)
        
        return {"summary_message": f"Farm detection complete: {len(qualifying_guilds)} {'server' if len(qualifying_guilds) == 1 else 'servers'} blacklisted, {total_transferred} {settings.collectible_name if total_transferred == 1 else settings.plural_collectible_name} transferred to escrow"}