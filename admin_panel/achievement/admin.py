from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.utils.safestring import mark_safe
from .models import Achievement, PlayerAchievement

@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "achievement_emoji_id", "enable", "self_catch", "required_quantity")
    list_editable = ("enable", "self_catch")
    search_fields = ("name",)
    autocomplete_fields  = ("required_balls", "reward", "special_required")

    @admin.display(description="Emojis")
    def achievement_emoji(self, obj: Achievement):
        return mark_safe(
            f'<img src="https://cdn.discordapp.com/emojis/{obj.achievement_emoji_id}.png?size=40" ' >
            f'title="ID: {obj.achievement_emoji_id}" />'
        )
@admin.register(PlayerAchievement)
class PlayerAchievementAdmin(admin.ModelAdmin):
    list_display = ("player", "achievement", "unlocked_at")
    list_filter = ("unlocked_at",)
    search_fields = ("player__discord_id", "achievement__name")

