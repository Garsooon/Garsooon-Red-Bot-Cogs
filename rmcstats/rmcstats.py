import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from redbot.core import commands

API_BASE = "https://api.retromc.org/api/v1/statistics"
MOJANG_LOOKUP = "https://api.mojang.com/users/profiles/minecraft/{}"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)

REPO_URL = "https://github.com/Garsooon/RMC-Stat-Cog"
GITHUB_ICON = "https://github.githubassets.com/favicons/favicon.png"
HEAD_URL = "https://mc-heads.net/avatar/{}/100"

LEADERBOARD_KEYS = {
    "playTime": "Play Time",
    "joinCount": "Joins",
    "creaturesKilled": "Creatures Killed",
    "playersKilled": "Players Killed",
    "blocksPlaced": "Blocks Placed",
    "blocksDestroyed": "Blocks Destroyed",
    "metersTraveled": "Meters Traveled",
    "playerDeaths": "Deaths",
    "itemsDropped": "Items Dropped",
    "trustScore": "Trust Score",
}
KEY_ALIASES = {
    "playTime": ["time", "playtime", "hours"],
    "joinCount": ["joins", "join", "logins"],
    "creaturesKilled": ["mobs", "mobkills", "kills", "creatures"],
    "playersKilled": ["pvp", "playerkills", "players"],
    "blocksPlaced": ["placed", "placements", "built"],
    "blocksDestroyed": ["destroyed", "broken", "mined", "blocksbroken"],
    "metersTraveled": ["distance", "meters", "traveled", "travelled"],
    "playerDeaths": ["deaths", "death", "died"],
    "itemsDropped": ["drops", "dropped", "items"],
    "trustScore": ["trust"],
}


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", text).lower()


KEYS_LOWER = {}
for _k, _label in LEADERBOARD_KEYS.items():
    for _name in (_k, _label, *KEY_ALIASES.get(_k, [])):
        KEYS_LOWER[_norm(_name)] = _k


def fmt_duration(seconds: int) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def fmt_value(key: str, value) -> str:
    if key == "playTime":
        return fmt_duration(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def ts(value: Optional[int], style: str = "D") -> str:
    return f"<t:{int(value)}:{style}>" if value else "Unknown"


def top_names(details: dict, n: int = 3) -> str:
    if not details:
        return "None"
    items = sorted(details.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return ", ".join(f"{k} ({v:,})" for k, v in items)


class RMCStats(commands.Cog):
    """RetroMC player statistics and leaderboards."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def cog_unload(self):
        await self.session.close()

    async def red_delete_data_for_user(self, **kwargs):
        return

    @staticmethod
    def _embed(title: str, description: str) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            url=REPO_URL,
            description=description,
            color=discord.Color.red(),
        )
        embed.set_footer(text=REPO_URL, icon_url=GITHUB_ICON)
        return embed

    async def _get_json(self, url: str, **params) -> Optional[dict]:
        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)

    async def _resolve_uuid(self, query: str) -> Optional[str]:
        query = query.strip()
        if UUID_RE.match(query):
            return query
        data = await self._get_json(f"{API_BASE}/leaderboard", key="playTime", limit=1000)
        if data and not data.get("error"):
            for entry in data.get("entries", []):
                if entry.get("username", "").lower() == query.lower():
                    return entry["uuid"]
        data = await self._get_json(MOJANG_LOOKUP.format(query))
        if data and data.get("id"):
            return data["id"]
        return None

    @commands.hybrid_command(name="stats")
    async def stats(self, ctx: commands.Context, player: str):
        """Show RetroMC stats for a player (username or UUID)."""
        async with ctx.typing():
            try:
                uuid = await self._resolve_uuid(player)
                if not uuid:
                    return await ctx.send(f"Couldn't find a player named `{player}`.")
                data = await self._get_json(f"{API_BASE}/player", uuid=uuid)
            except aiohttp.ClientError:
                return await ctx.send("Couldn't reach the RetroMC API. Try again later.")

        if not data or data.get("error"):
            return await ctx.send(f"No stats found for `{player}`.")

        s = data.get("stats", {})
        d = data.get("details", {})
        online = data.get("online")
        name = data.get("username", player)
        placed = top_names(d.get("blockDetailsPlaced"))
        destroyed = top_names(d.get("blockDetailsDestroyed"))
        description = (
            f"**{name}** is currently {'online' if online else 'offline'}\n"
            f"**Play time:** {fmt_duration(s.get('playTime', 0))} • "
            f"**Joins:** {s.get('joinCount', 0):,}\n"
            f"**First join:** {ts(s.get('firstJoin'))} • **Last join:** {ts(s.get('lastJoin'))}\n"
            f"**Players killed:** {s.get('playersKilled', 0):,} • "
            f"**Creatures killed:** {s.get('creaturesKilled', 0):,} • "
            f"**Deaths:** {s.get('playerDeaths', 0):,}\n"
            f"**Blocks placed:** {s.get('blocksPlaced', 0):,} • "
            f"**Destroyed:** {s.get('blocksDestroyed', 0):,}\n"
            f"**Items dropped:** {s.get('itemsDropped', 0):,} • "
            f"**Distance:** {s.get('metersTraveled', 0):,} m\n"
            f"**Money:** {s.get('money', 0):,.2f} • "
            f"**Trust:** {s.get('trustScore', 0)} (level {s.get('trustLevel', 0)})\n"
            f"**Groups:** {', '.join(s.get('groups') or []) or 'None'}\n"
            f"**Top blocks placed:** {placed}\n"
            f"**Top blocks destroyed:** {destroyed}"
        )
        embed = self._embed(f"RetroMC Stats: {name}", description)
        embed.set_thumbnail(url=HEAD_URL.format(name))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard")
    async def leaderboard(self, ctx: commands.Context, stat: str = "playTime", limit: int = 10):
        """Show a RetroMC leaderboard.

        Stats: playTime, joinCount, creaturesKilled, playersKilled, blocksPlaced,
        blocksDestroyed, metersTraveled, playerDeaths, itemsDropped, trustScore

        Aliases also work, e.g. deaths, pvp, mobs, distance, trust, joins.
        """
        key = KEYS_LOWER.get(_norm(stat))
        if not key:
            return await ctx.send("Unknown stat. Options: " + ", ".join(f"`{k}`" for k in LEADERBOARD_KEYS))
        limit = max(1, min(limit, 25))

        async with ctx.typing():
            try:
                data = await self._get_json(f"{API_BASE}/leaderboard", key=key, limit=limit)
            except aiohttp.ClientError:
                return await ctx.send("Couldn't reach the RetroMC API. Try again later.")

        if not data or data.get("error") or not data.get("entries"):
            return await ctx.send("No leaderboard data available.")

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for e in data["entries"]:
            rank = medals.get(e["rank"], f"`#{e['rank']}`")
            lines.append(f"{rank} **{e['username']}** — {fmt_value(key, e['value'])}")
        embed = self._embed(
            f"RetroMC Leaderboard: {LEADERBOARD_KEYS[key]}",
            f"Top **{len(lines)}** players by {LEADERBOARD_KEYS[key].lower()}\n" + "\n".join(lines),
        )
        embed.set_thumbnail(url=HEAD_URL.format(data["entries"][0]["username"]))
        await ctx.send(embed=embed)

    @leaderboard.autocomplete("stat")
    async def _stat_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            discord.app_commands.Choice(name=label, value=k)
            for k, label in LEADERBOARD_KEYS.items()
            if current.lower() in k.lower() or current.lower() in label.lower()
        ][:25]
