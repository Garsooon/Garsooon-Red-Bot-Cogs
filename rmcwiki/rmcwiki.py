"""RMCWiki — a Red-DiscordBot cog that searches the RetroMC wiki.

Displays the nearest matching page for a query as a red embed with a short
extract and a link, plus any near-miss suggestions.
"""

import asyncio
from urllib.parse import quote
from typing import List, Optional, Tuple

import aiohttp
import discord
from redbot.core import commands

WIKI_BASE = "https://wiki.retromc.org"
API_URL = f"{WIKI_BASE}/api.php"

# RetroMC red.
EMBED_COLOR = discord.Color.from_rgb(200, 30, 30)

# Play nice with the wiki: identify ourselves.
USER_AGENT = "RMCWikiCog/1.0 (Red-DiscordBot; +https://wiki.retromc.org)"

REQUEST_TIMEOUT = 15


class RMCWiki(commands.Cog):
    """Search the RetroMC wiki (https://wiki.retromc.org) from Discord."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        )

    def cog_unload(self):
        # ClientSession must be closed on unload; schedule it on the loop.
        asyncio.create_task(self.session.close())

    async def _api(self, params: dict) -> Optional[dict]:
        """Call the MediaWiki API, returning parsed JSON or None on failure."""
        params = {**params, "format": "json", "formatversion": "2"}
        try:
            async with self.session.get(API_URL, params=params) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def _search(self, query: str, limit: int = 5) -> List[str]:
        """Return page titles best matching the query, nearest first."""
        data = await self._api(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                # Rank on the title/opening as well as full text so the
                # "nearest" result feels intuitive for short queries.
                "srwhat": "text",
            }
        )
        if not data:
            return []
        hits = data.get("query", {}).get("search", [])
        titles = [h["title"] for h in hits]
        if titles:
            return titles

        # Fall back to a prefix/near-match search if full-text found nothing.
        data = await self._api(
            {
                "action": "opensearch",
                "search": query,
                "limit": limit,
                "redirects": "resolve",
            }
        )
        if isinstance(data, list) and len(data) >= 2:
            return list(data[1])
        return []

    async def _page(self, title: str) -> Optional[Tuple[str, str, str, Optional[str]]]:
        """Return (title, extract, url, thumbnail) for a page title."""
        data = await self._api(
            {
                "action": "query",
                "prop": "extracts|info|pageimages",
                "titles": title,
                "exintro": 1,
                "explaintext": 1,
                "exchars": 600,
                "inprop": "url",
                "piprop": "thumbnail",
                "pithumbsize": 300,
                "redirects": 1,
            }
        )
        if not data:
            return None
        pages = data.get("query", {}).get("pages", [])
        if not pages:
            return None
        page = pages[0]
        if page.get("missing"):
            return None
        extract = (page.get("extract") or "").strip()
        url = page.get("fullurl") or f"{WIKI_BASE}/{quote(title.replace(' ', '_'))}"
        thumb = (page.get("thumbnail") or {}).get("source")
        return page.get("title", title), extract, url, thumb

    @commands.command(name="wiki", aliases=["rmcwiki", "rmc"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def wiki(self, ctx: commands.Context, *, query: str):
        """Search the RetroMC wiki and show the nearest matching page.

        Example: `[p]wiki spawn subway`
        """
        async with ctx.typing():
            titles = await self._search(query)
            if not titles:
                embed = discord.Embed(
                    title="No results",
                    description=(
                        f"Nothing on the RetroMC wiki matched **{query}**.\n"
                        f"[Search the wiki yourself]"
                        f"({WIKI_BASE}/index.php?search={quote(query)})"
                    ),
                    color=EMBED_COLOR,
                )
                await ctx.send(embed=embed)
                return

            nearest = titles[0]
            page = await self._page(nearest)

        if page is None:
            await ctx.send(
                f"Found **{nearest}** but couldn't load it. "
                f"<{WIKI_BASE}/{quote(nearest.replace(' ', '_'))}>"
            )
            return

        title, extract, url, thumb = page
        if not extract:
            extract = "*(This page has no summary text.)*"

        embed = discord.Embed(
            title=title,
            url=url,
            description=extract,
            color=EMBED_COLOR,
        )
        embed.set_author(name="RetroMC Wiki", url=WIKI_BASE)
        if thumb:
            embed.set_thumbnail(url=thumb)

        # Offer the other near matches so people can jump to a better page.
        others = [t for t in titles[1:] if t != title][:4]
        if others:
            links = "\n".join(
                f"• [{t}]({WIKI_BASE}/{quote(t.replace(' ', '_'))})" for t in others
            )
            embed.add_field(name="Other matches", value=links, inline=False)

        embed.set_footer(text=f"Nearest match for: {query}")
        await ctx.send(embed=embed)

    @wiki.error
    async def wiki_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send_help(ctx.command)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"Slow down — try again in {error.retry_after:.0f}s.",
                delete_after=5,
            )
        else:
            raise error
