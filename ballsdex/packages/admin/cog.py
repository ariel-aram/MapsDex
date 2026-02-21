from collections import defaultdict
from typing import TYPE_CHECKING, cast
import io
import logging
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button

from ballsdex.core.models import Ball, GuildConfig, balls
from ballsdex.core.utils.paginator import FieldPageSource, Pages, TextPageSource
from ballsdex.core.utils.logging import log_action
from ballsdex.settings import settings

from .balls import Balls as BallsGroup
from .blacklist import Blacklist as BlacklistGroup
from .blacklist import BlacklistGuild as BlacklistGuildGroup
from .history import History as HistoryGroup
from .info import Info as InfoGroup
from .logs import Logs as LogsGroup
from .logging_decorator import log_admin_command

log = logging.getLogger("ballsdex.packages.admin.cog")

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.countryballs.cog import CountryBallsSpawner
    from ballsdex.packages.trade.cog import Trade


@app_commands.guilds(*settings.admin_guild_ids)
@app_commands.default_permissions(administrator=True)
class Admin(commands.GroupCog):
    """
    Bot admin commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

        assert self.__cog_app_commands_group__
        self.__cog_app_commands_group__.add_command(
            BallsGroup(name=settings.players_group_cog_name)
        )
        self.__cog_app_commands_group__.add_command(BlacklistGroup())
        self.__cog_app_commands_group__.add_command(BlacklistGuildGroup())
        self.__cog_app_commands_group__.add_command(HistoryGroup())
        self.__cog_app_commands_group__.add_command(LogsGroup())
        self.__cog_app_commands_group__.add_command(InfoGroup())

    async def get_broadcast_channels(self):
        """Get all ball spawn channels for broadcasting"""
        try:
            channels = set()
            async for config in GuildConfig.filter(enabled=True, spawn_channel__isnull=False):
                channel = self.bot.get_channel(config.spawn_channel)
                if channel:
                    channels.add(config.spawn_channel)
                else:
                    try:
                        config.enabled = False
                        await config.save()
                    except Exception:
                        pass
            return channels
        except Exception as e:
            log.error(f"Error getting broadcast channels: {str(e)}")
            return set()

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @log_admin_command()
    async def status(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        status: discord.Status | None = None,
        name: str | None = None,
        state: str | None = None,
        activity_type: discord.ActivityType | None = None,
    ):
        """
        Change the status of the bot. Provide at least status or text.

        Parameters
        ----------
        status: discord.Status
            The status you want to set
        name: str
            Title of the activity, if not custom
        state: str
            Custom status or subtitle of the activity
        activity_type: discord.ActivityType
            The type of activity
        """
        if not status and not name and not state:
            await interaction.response.send_message(
                "You must provide at least `status`, `name` or `state`.", ephemeral=True
            )
            return

        activity: discord.Activity | None = None
        status = status or discord.Status.online
        activity_type = activity_type or discord.ActivityType.custom

        if activity_type == discord.ActivityType.custom and name and not state:
            await interaction.response.send_message(
                "You must provide `state` for custom activities. `name` is unused.", ephemeral=True
            )
            return
        if activity_type != discord.ActivityType.custom and not name:
            await interaction.response.send_message(
                "You must provide `name` for pre-defined activities.", ephemeral=True
            )
            return
        if name or state:
            activity = discord.Activity(name=name or state, state=state, type=activity_type)
        await self.bot.change_presence(status=status, activity=activity)
        await interaction.response.send_message("Status updated.", ephemeral=True)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @log_admin_command()
    async def trade_lockdown(
        self, interaction: discord.Interaction["BallsDexBot"], *, reason: str
    ):
        """
        Cancel all ongoing trades and lock down further trades from being started.

        Parameters
        ----------
        reason: str
            The reason of the lockdown. This will be displayed to all trading users.
        """
        cog = cast("Trade | None", self.bot.get_cog("Trade"))
        if not cog:
            await interaction.response.send_message("The trade cog is not loaded.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        result = await cog.cancel_all_trades(reason)

        assert self.bot.user
        prefix = (
            settings.prefix if self.bot.intents.message_content else f"{self.bot.user.mention} "
        )

        if not result:
            await interaction.followup.send(
                "All trades were successfully cancelled, and further trades cannot be started "
                f'anymore.\nTo enable trades again, the bot owner must use the "{prefix}reload '
                'trade" command.'
            )
        else:
            await interaction.followup.send(
                "Lockdown mode enabled, trades can no longer be started. "
                f"While cancelling ongoing trades, {len(result)} failed to cancel, check your "
                "logs for info.\nTo enable trades again, the bot owner must use the "
                f'"{prefix}reload trade" command.'
            )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command()
    async def rarity(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        include_disabled: bool = False,
    ):
        """
        Generate a list of countryballs ranked by rarity.

        Parameters
        ----------
        include_disabled: bool
            Include the countryballs that are disabled or with a rarity of 0.
        """
        text = ""
        balls_queryset = Ball.all().order_by("rarity")
        if not include_disabled:
            balls_queryset = balls_queryset.filter(rarity__gt=0, enabled=True)
        sorted_balls = await balls_queryset  # ordered by rarity ascending
        
        groups = defaultdict(list)  # preserves insertion order on iteration
        for ball in sorted_balls:
            groups[ball.rarity].append(ball)

        tier = 1
        lines = []
        for _, chunk in groups.items():  # iterates in first-seen rarity order
            lines.append(f"T{tier}:")
            for b in chunk:
                lines.append(f"{b.country}")  # no numbering
            lines.append("")
            tier += 1
        text = "\n".join(lines).rstrip()

        source = TextPageSource(text, prefix="```md\n", suffix="```")
        pages = Pages(source=source, interaction=interaction, compact=True)
        pages.remove_item(pages.stop_pages)
        await pages.start(ephemeral=True)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command()
    async def cooldown(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        guild_id: str | None = None,
    ):
        """
        Show the details of the spawn cooldown system for the given server

        Parameters
        ----------
        guild_id: int | None
            ID of the server you want to inspect. If not given, inspect the current server.
        """
        if guild_id:
            try:
                guild = self.bot.get_guild(int(guild_id))
            except ValueError:
                await interaction.response.send_message(
                    "Invalid guild ID. Please make sure it's a number.", ephemeral=True
                )
                return
        else:
            guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "The given guild could not be found.", ephemeral=True
            )
            return

        spawn_manager = cast(
            "CountryBallsSpawner", self.bot.get_cog("CountryBallsSpawner")
        ).spawn_manager
        await spawn_manager.admin_explain(interaction, guild)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command()
    async def guilds(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
    ):
        """
        Shows the guilds shared with the specified user. Provide either user or user_id.

        Parameters
        ----------
        user: discord.User
            The user you want to check, if available in the current server.
        """
        if self.bot.intents.members:
            guilds = user.mutual_guilds
        else:
            guilds = [x for x in self.bot.guilds if x.owner_id == user.id]

        if not guilds:
            if self.bot.intents.members:
                await interaction.response.send_message(
                    f"The user does not own any server with {settings.bot_name}.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"The user does not own any server with {settings.bot_name}.\n"
                    ":warning: *The bot cannot be aware of the member's presence in servers, "
                    "it is only aware of server ownerships.*",
                    ephemeral=True,
                )
            return

        entries: list[tuple[str, str]] = []
        for guild in guilds:
            if config := await GuildConfig.get_or_none(guild_id=guild.id):
                spawn_enabled = config.enabled and config.guild_id
            else:
                spawn_enabled = False

            field_name = f"`{guild.id}`"
            field_value = ""

            # highlight suspicious server names
            if any(x in guild.name.lower() for x in ("farm", "grind", "spam")):
                field_value += f"- :warning: **{guild.name}**\n"
            else:
                field_value += f"- {guild.name}\n"

            # highlight low member count
            if guild.member_count <= 3:  # type: ignore
                field_value += f"- :warning: **{guild.member_count} members**\n"
            else:
                field_value += f"- {guild.member_count} members\n"

            # highlight if spawning is enabled
            if spawn_enabled:
                field_value += "- :warning: **Spawn is enabled**"
            else:
                field_value += "- Spawn is disabled"

            entries.append((field_name, field_value))

        source = FieldPageSource(entries, per_page=25, inline=True)
        source.embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)

        if len(guilds) > 1:
            source.embed.title = f"{len(guilds)} servers shared"
        else:
            source.embed.title = "1 server shared"

        if not self.bot.intents.members:
            source.embed.set_footer(
                text="\N{WARNING SIGN} The bot cannot be aware of the member's "
                "presence in servers, it is only aware of server ownerships."
            )

        pages = Pages(source=source, interaction=interaction, compact=True)
        pages.add_item(
            Button(
                style=discord.ButtonStyle.link,
                label="View profile",
                url=f"discord://-/users/{user.id}",
                emoji="\N{LEFT-POINTING MAGNIFYING GLASS}",
            )
        )
        await pages.start(ephemeral=True)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @log_admin_command()
    async def say(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        message: str,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        """
        Send a message as the bot to a specified channel.

        Parameters
        ----------
        message: str
            The message to send
        channel: discord.TextChannel | discord.Thread | None
            The channel or thread to send the message to. Defaults to current channel if not specified.
        """
        # Get target channel
        target_channel = channel if channel else interaction.channel
        
        # Verify it's a valid messageable channel/thread
        if not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Invalid channel type. Must be a text channel or thread.", ephemeral=True
            )
            return
        
        try:
            await target_channel.send(message)
            
            # Get guild name safely (threads have parent channels)
            guild_name = target_channel.guild.name if hasattr(target_channel, 'guild') else "Unknown"
            
            await interaction.response.send_message(
                f"Message sent to {target_channel.mention} ({guild_name})", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"Missing permissions to send messages in {target_channel.mention}", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to send message: {str(e)}", ephemeral=True
            )

    @app_commands.command(name="broadcast", description="Send a broadcast message to all ball spawn channels")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @app_commands.choices(broadcast_type=[
        app_commands.Choice(name="Text and Image", value="both"),
        app_commands.Choice(name="Text Only", value="text"),
        app_commands.Choice(name="Image Only", value="image")
    ])
    @log_admin_command()
    async def broadcast(
        self, 
        interaction: discord.Interaction["BallsDexBot"], 
        broadcast_type: str,
        message: str | None = None,
        attachment: discord.Attachment | None = None,
        anonymous: bool = False
    ):
        """Send broadcast messages to all ball spawn channels"""
        if broadcast_type == "text" and not message:
            await interaction.response.send_message("You must provide a message when selecting 'Text Only' mode.", ephemeral=True)
            return
        if broadcast_type == "image" and not attachment:
            await interaction.response.send_message("You must provide an image when selecting 'Image Only' mode.", ephemeral=True)
            return
        if broadcast_type == "both" and not message and not attachment:
            await interaction.response.send_message("You must provide a message or image when selecting 'Text and Image' mode.", ephemeral=True)
            return

        try:
            channels = await self.get_broadcast_channels()
            if not channels:
                await interaction.response.send_message("No ball spawn channels are currently configured.", ephemeral=True)
                return

            await interaction.response.send_message("Broadcasting message...", ephemeral=True)
            
            success_count = 0
            fail_count = 0
            failed_channels = []
            
            broadcast_message = None
            if message:
                broadcast_message = (
                    "🔔 **System Announcement** 🔔\n"
                    "------------------------\n"
                    f"{message}\n"
                    "------------------------\n"
                )
                if not anonymous:
                    broadcast_message += f"*Sent by {interaction.user.name}*"
            
            file = None
            file_data = None
            if attachment and broadcast_type in ["both", "image"]:
                try:
                    file_data = await attachment.read()
                    file = await attachment.to_file()
                except Exception as e:
                    log.error(f"Error downloading attachment: {str(e)}")
                    await interaction.followup.send("An error occurred while downloading the attachment. Only the text message will be sent.", ephemeral=True)
            
            for channel_id in channels:
                try:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        if broadcast_type == "text":
                            await channel.send(broadcast_message)
                        elif broadcast_type == "image" and file_data:
                            new_file = discord.File(
                                io.BytesIO(file_data),
                                filename=attachment.filename,
                                spoiler=attachment.is_spoiler()
                            )
                            await channel.send(file=new_file)
                        else:  # both
                            if file_data and broadcast_message:
                                new_file = discord.File(
                                    io.BytesIO(file_data),
                                    filename=attachment.filename,
                                    spoiler=attachment.is_spoiler()
                                )
                                await channel.send(broadcast_message, file=new_file)
                            elif file_data:
                                new_file = discord.File(
                                    io.BytesIO(file_data),
                                    filename=attachment.filename,
                                    spoiler=attachment.is_spoiler()
                                )
                                await channel.send(file=new_file)
                            elif broadcast_message:
                                await channel.send(broadcast_message)
                        success_count += 1
                    else:
                        fail_count += 1
                        failed_channels.append(f"Channel ID: {channel_id}")
                except Exception as e:
                    log.error(f"Error sending to channel {channel_id}: {str(e)}")
                    fail_count += 1
                    failed_channels.append(f"Channel ID: {channel_id}")
            
            result_message = f"Broadcast complete!\nSuccessfully sent to: {success_count} channels\nFailed: {fail_count} channels"
            if failed_channels:
                result_message += "\n\nFailed channels:\n" + "\n".join(failed_channels[:10])
                if len(failed_channels) > 10:
                    result_message += f"\n... and {len(failed_channels) - 10} more"
            
            await interaction.followup.send(result_message, ephemeral=True)
                
        except Exception as e:
            log.error(f"Error in broadcast: {str(e)}")
            await interaction.response.send_message("An error occurred while executing the command. Please try again later.", ephemeral=True)

    @app_commands.command(name="broadcast_dm", description="Send a DM broadcast to specific users")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @log_admin_command()
    async def broadcast_dm(
        self, 
        interaction: discord.Interaction["BallsDexBot"], 
        message: str,
        user_ids: str,
        anonymous: bool = False
    ):
        """Private Message Broadcasting to Specified users
        
        Args:
            message: the message you are going to send
            user_ids: a comma-separated list of user IDs to send the message to
            anonymous: gives an option to send the message anonymously
        """
        try:
            user_id_list = [uid.strip() for uid in user_ids.split(",")]
            if not user_id_list:
                await interaction.response.send_message("Please provide at least one user ID.", ephemeral=True)
                return

            await interaction.response.send_message("Starting DM broadcast...", ephemeral=True)
            
            success_count = 0
            fail_count = 0
            failed_users = []
            
            dm_message = (
                "🔔 **System DM** 🔔\n"
                "------------------------\n"
                f"{message}\n"
                "------------------------\n"
            )
            if not anonymous:
                dm_message += f"*Sent by {interaction.user.name}*"
            
            for user_id in user_id_list:
                try:
                    user = await self.bot.fetch_user(int(user_id))
                    if user:
                        await user.send(dm_message)
                        success_count += 1
                    else:
                        fail_count += 1
                        failed_users.append(f"Unknown User (ID: {user_id})")
                except Exception as e:
                    log.error(f"Error sending DM to user {user_id}: {str(e)}")
                    fail_count += 1
                    failed_users.append(f"User ID: {user_id}")
            
            result_message = f"DM broadcast complete!\nSuccessfully sent: {success_count} users\nFailed: {fail_count} users"
            if failed_users:
                result_message += "\n\nFailed users:\n" + "\n".join(failed_users[:10])
                if len(failed_users) > 10:
                    result_message += f"\n... and {len(failed_users) - 10} more"
            
            await interaction.followup.send(result_message, ephemeral=True)
                
        except Exception as e:
            log.error(f"Error in broadcast_dm: {str(e)}")
            try:
                await interaction.followup.send("An error occurred while executing the command. Please try again later.", ephemeral=True)
            except Exception:
                pass