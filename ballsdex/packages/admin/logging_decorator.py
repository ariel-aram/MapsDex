import functools
import logging
from typing import Any, Callable, Coroutine, TypeVar

import discord
from discord import app_commands

from ballsdex.core.bot import BallsDexBot
from ballsdex.core.utils.logging import log_action

log = logging.getLogger("ballsdex.packages.admin")

T = TypeVar('T', bound=Callable[..., Coroutine[Any, Any, Any]])


def log_admin_command(log_summary: bool = False) -> Callable[[T], T]:
    """
    Decorator to log all admin command executions to both Docker and Discord channel.
    This ensures that even if staff delete logs, the commands will still appear in Docker.
    
    Parameters:
    -----------
    log_summary: bool
        If True, the decorator will also look for a 'summary_message' return value
        from the command and log it as well. Useful for commands that need to log
        results/impact in addition to the command execution.
    """
    def decorator(func: T) -> T:
        @functools.wraps(func)
        async def wrapper(
            *args: Any,
            **kwargs: Any
        ) -> Any:
            # Find the interaction parameter (it's the first discord.Interaction in args)
            interaction: discord.Interaction["BallsDexBot"] | None = None
            for arg in args:
                if isinstance(arg, discord.Interaction):
                    interaction = arg
                    break
            
            if not interaction:
                # If we can't find interaction, execute the function without logging
                return await func(*args, **kwargs)
            
            # Extract command name and arguments for logging
            # Use the actual command name from interaction data if available
            if interaction.data and 'name' in interaction.data:
                # Get the root command name from interaction data
                command_name = interaction.data['name']
                
                # Check if this is a subcommand by looking at the options
                if 'options' in interaction.data and interaction.data['options']:
                    for option in interaction.data['options']:
                        if option.get('type') == 1:  # SUB_COMMAND type
                            # This is a subcommand, use the subcommand name
                            command_name = f"{command_name} {option['name']}"
                            break
            else:
                # Fallback to function name if interaction data is not available
                command_name = func.__name__
            
            # Build a string representation of arguments (skip self and interaction)
            args_str = []
            # Skip first arg (self) and interaction
            relevant_args = args[1:] if args and len(args) > 1 else []
            relevant_args = [arg for arg in relevant_args if arg is not interaction]
            
            if relevant_args:
                for i, arg in enumerate(relevant_args):
                    if hasattr(arg, 'name'):  # User, Guild, Channel objects
                        args_str.append(f"{type(arg).__name__}({arg.name})")
                    elif hasattr(arg, 'id'):  # Objects with ID
                        args_str.append(f"{type(arg).__name__}(id={arg.id})")
                    else:
                        args_str.append(repr(arg))
            
            # Handle keyword arguments
            if kwargs:
                for key, value in kwargs.items():
                    if hasattr(value, 'name'):
                        args_str.append(f"{key}={type(value).__name__}({value.name})")
                    elif hasattr(value, 'id'):
                        args_str.append(f"{key}={type(value).__name__}(id={value.id})")
                    else:
                        args_str.append(f"{key}={repr(value)}")
            
            # Create the log message
            guild_info = f" in guild {interaction.guild.name}({interaction.guild.id})" if interaction.guild else " in DMs"
            channel_info = f" channel {interaction.channel.name}({interaction.channel.id})" if interaction.channel else ""
            full_command = f"/admin {command_name}"
            
            # Create detailed log message for Discord
            discord_message = (
                f"**{interaction.user}** ({interaction.user.id}) used `{full_command}`"
                f"{guild_info}{channel_info}"
            )
            
            if args_str:
                discord_message += f"\n**Arguments:** {', '.join(args_str)}"
            
            # Create shorter log message for Docker
            docker_message = (
                f"ADMIN COMMAND: {interaction.user}({interaction.user.id}) used {full_command}"
                f"{guild_info}{channel_info}"
            )
            
            if args_str:
                docker_message += f" with args: {', '.join(args_str)}"
            
            # Execute the original command first to get the result (and potential summary)
            try:
                result = await func(*args, **kwargs)
                
                # Check if we have a summary message to include
                summary_message = None
                if log_summary and result and isinstance(result, dict):
                    summary_message = result.get('summary_message')
                
                # Create the final log message (with or without summary)
                if summary_message:
                    # Combine command and summary into a single log message
                    docker_message_with_summary = (
                        f"{docker_message}\nADMIN SUMMARY: {summary_message}"
                    )
                    discord_message_with_summary = (
                        f"{discord_message}\n📊 {summary_message}"
                    )
                    
                    # Log combined message to Docker
                    log.info(docker_message_with_summary)
                    
                    # Log combined message to Discord
                    try:
                        await log_action(discord_message_with_summary, interaction.client)
                    except Exception as e:
                        # Don't let Discord logging failures break the command
                        log.error(f"Failed to log to Discord channel: {str(e)}")
                else:
                    # No summary, log just the command as before
                    # Log to Docker with info level
                    log.info(docker_message)
                    
                    # Log to Discord channel (if configured)
                    try:
                        await log_action(discord_message, interaction.client)
                    except Exception as e:
                        # Don't let Discord logging failures break the command
                        log.error(f"Failed to log to Discord channel: {str(e)}")
                
                return result
            except Exception as e:
                # Log errors with error level as well
                error_message = (
                    f"ADMIN COMMAND ERROR: {interaction.user}({interaction.user.id}) "
                    f"used {full_command} - Error: {str(e)}"
                )
                log.error(error_message)
                
                # Also log errors to Discord
                try:
                    await log_action(f"❌ {error_message}", interaction.client)
                except Exception:
                    pass
                
                raise
        
        return wrapper  # type: ignore
    
    return decorator
