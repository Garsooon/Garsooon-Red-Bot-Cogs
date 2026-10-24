import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.mod import is_admin_or_superior

log = logging.getLogger("red.githubfeed")

API = "https://api.github.com"
EVENTS = ("release", "commit", "pr")
MAX_POSTS_PER_REPO = 10
GITHUB_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)", re.I)
MAX_TRACKED_PRS = 100
DEFAULT_INTERVAL = 300
MIN_INTERVAL = 60

COLOR_RELEASE = discord.Color.green()
COLOR_COMMIT = discord.Color.blurple()
PR_COLORS = {
    "open": discord.Color.green(),
    "merged": discord.Color.purple(),
    "closed": discord.Color.red(),
}


class GithubFeed(commands.Cog):
    """Post GitHub releases, commits and pull requests to a channel."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x67687F33D, force_registration=True)
        self.config.register_global(interval=DEFAULT_INTERVAL)
        self.config.register_guild(repos={})
        self.session: Optional[aiohttp.ClientSession] = None
        self._etags: Dict[str, str] = {}

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()
        interval = await self.config.interval()
        self.poll.change_interval(seconds=max(interval, MIN_INTERVAL))
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        if self.session:
            await self.session.close()

    async def red_delete_data_for_user(self, **kwargs) -> None:
        return

    async def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Red-GithubFeed",
        }
        tokens = await self.bot.get_shared_api_tokens("github")
        token = tokens.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _get(
        self, path: str, params: Optional[Dict[str, Any]] = None, use_etag: bool = True
    ) -> Tuple[int, Any]:
        """Return (status, json). Status 304 means unchanged since the last call."""
        headers = await self._headers()
        key = path + repr(sorted((params or {}).items()))
        if use_etag and key in self._etags:
            headers["If-None-Match"] = self._etags[key]
        async with self.session.get(f"{API}{path}", params=params, headers=headers) as resp:
            if resp.status == 401 and "Authorization" in headers:
                # Stale or revoked token: public repos still work without one.
                log.warning("GitHub rejected the stored token; retrying %s unauthenticated", path)
                headers.pop("Authorization")
                headers.pop("If-None-Match", None)
                async with self.session.get(f"{API}{path}", params=params, headers=headers) as retry:
                    if retry.status == 200:
                        return 200, await retry.json()
                    return retry.status, None
            if resp.status == 200:
                etag = resp.headers.get("ETag")
                if etag:
                    self._etags[key] = etag
                return 200, await resp.json()
            if resp.status in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
                log.warning("GitHub rate limit reached; resets at %s", resp.headers.get("X-RateLimit-Reset"))
            return resp.status, None

    @tasks.loop(seconds=DEFAULT_INTERVAL)
    async def poll(self) -> None:
        await self._poll_all()

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_red_ready()

    @poll.error
    async def _poll_error(self, error: BaseException) -> None:
        log.exception("Poll loop crashed; restarting", exc_info=error)
        if not self.poll.is_running():
            self.poll.restart()

    async def _poll_all(self, only: Optional[List[discord.Guild]] = None) -> int:
        """Poll tracked repos (all guilds, or just `only`). Returns the number of embeds posted."""
        posted = 0
        allowed = {g.id for g in only} if only is not None else None
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None or (allowed is not None and guild_id not in allowed):
                continue
            for repo in list(data.get("repos", {})):
                try:
                    posted += await self._poll_repo(guild, repo)
                except Exception:
                    log.exception("Error polling %s in guild %s", repo, guild_id)
        return posted

    async def _poll_repo(self, guild: discord.Guild, repo: str) -> int:
        async with self.config.guild(guild).repos() as repos:
            state = repos.get(repo)
            if state is None:
                return 0
            events: List[discord.Embed] = []
            if "release" in state["events"]:
                events += await self._check_releases(repo, state)
            if "commit" in state["events"]:
                events += await self._check_commits(repo, state)
            if "pr" in state["events"]:
                events += await self._check_prs(repo, state)
            channel = guild.get_channel(state["channel_id"])
            events = events[:MAX_POSTS_PER_REPO * 3]
            if not state.get("links", True):
                for embed in events:
                    embed.url = None
        if not events or not isinstance(channel, discord.abc.Messageable):
            return 0
        sent = 0
        for embed in events:
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                sent += 1
            except discord.Forbidden:
                log.info("Missing permission to post in #%s (%s)", channel, guild)
                break
            except discord.HTTPException:
                log.exception("Failed sending embed for %s", repo)
        return sent

    async def _check_releases(self, repo: str, state: dict) -> List[discord.Embed]:
        status, data = await self._get(f"/repos/{repo}/releases", {"per_page": 10})
        if status != 200 or data is None:
            return []
        releases = [r for r in data if not r.get("draft")]
        if not releases:
            state.setdefault("last_release_id", 0)
            return []
        newest = max(r["id"] for r in releases)
        last = state.get("last_release_id")
        state["last_release_id"] = max(newest, last or 0)
        if last is None:
            return []
        new = sorted((r for r in releases if r["id"] > last), key=lambda r: r["id"])
        return [self._release_embed(self._label(repo, state), r) for r in new[-MAX_POSTS_PER_REPO:]]

    async def _check_commits(self, repo: str, state: dict) -> List[discord.Embed]:
        params = {"per_page": 20}
        if state.get("branch"):
            params["sha"] = state["branch"]
        status, data = await self._get(f"/repos/{repo}/commits", params)
        if status != 200 or not data:
            return []
        last = state.get("last_commit_sha")
        state["last_commit_sha"] = data[0]["sha"]
        if last is None:
            return []
        new = []
        for commit in data:
            if commit["sha"] == last:
                break
            new.append(commit)
        return [self._commit_embed(self._label(repo, state), c, state.get("privacy", False)) for c in reversed(new[:MAX_POSTS_PER_REPO])]

    async def _check_prs(self, repo: str, state: dict) -> List[discord.Embed]:
        status, data = await self._get(
            f"/repos/{repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 20},
        )
        if status != 200 or data is None:
            return []
        seen: Optional[Dict[str, str]] = state.get("pr_states")
        current = {str(pr["number"]): self._pr_state(pr) for pr in data}
        if seen is None:
            state["pr_states"] = current
            return []
        changed = [
            pr for pr in reversed(data)
            if seen.get(str(pr["number"])) != current[str(pr["number"])]
        ]
        seen.update(current)
        if len(seen) > MAX_TRACKED_PRS:
            for key in sorted(seen, key=int)[: len(seen) - MAX_TRACKED_PRS]:
                del seen[key]
        return [self._pr_embed(self._label(repo, state), pr) for pr in changed[-MAX_POSTS_PER_REPO:]]

    @staticmethod
    def _label(repo: str, state: dict) -> str:
        return state.get("alias") or repo

    @staticmethod
    def _pr_state(pr: dict) -> str:
        if pr.get("merged_at"):
            return "merged"
        return pr["state"]

    @staticmethod
    def _clip(text: Optional[str], limit: int) -> str:
        text = text or ""
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _release_embed(self, repo: str, rel: dict) -> discord.Embed:
        name = rel.get("name") or rel["tag_name"]
        embed = discord.Embed(
            title=self._clip(f"{repo} {name}", 256),
            url=rel["html_url"],
            description=self._clip(rel.get("body"), 4000),
            color=COLOR_RELEASE,
            timestamp=discord.utils.parse_time(rel.get("published_at")),
        )
        kind = "Pre-release" if rel.get("prerelease") else "Release"
        embed.set_author(name=f"{kind} published", icon_url=(rel.get("author") or {}).get("avatar_url"))
        embed.add_field(name="Tag", value=f"`{rel['tag_name']}`")
        return embed

    def _commit_embed(self, repo: str, commit: dict, private: bool = False) -> discord.Embed:
        info = commit["commit"]
        message = info["message"]
        title, _, body = message.partition("\n")
        author = commit.get("author") or {}
        embed = discord.Embed(
            title=self._clip(f"[{repo}] {title}", 256),
            url=None if private else commit["html_url"],
            description=self._clip(body.strip(), 1000) or None,
            color=COLOR_COMMIT,
            timestamp=discord.utils.parse_time(info["author"]["date"]),
        )
        embed.set_author(
            name=author.get("login") or info["author"]["name"],
            icon_url=author.get("avatar_url"),
        )
        if not private:
            embed.set_footer(text=commit["sha"][:7])
        return embed

    def _pr_embed(self, repo: str, pr: dict) -> discord.Embed:
        state = self._pr_state(pr)
        verb = {"open": "opened", "merged": "merged", "closed": "closed"}[state]
        embed = discord.Embed(
            title=self._clip(f"[{repo}] PR #{pr['number']}: {pr['title']}", 256),
            url=pr["html_url"],
            description=self._clip(pr.get("body"), 800) or None,
            color=PR_COLORS[state],
            timestamp=discord.utils.parse_time(pr["updated_at"]),
        )
        user = pr.get("user") or {}
        embed.set_author(name=f"{user.get('login', 'unknown')} {verb} a pull request", icon_url=user.get("avatar_url"))
        embed.add_field(name="Branches", value=f"`{pr['head']['ref']}` → `{pr['base']['ref']}`")
        embed.set_footer(text=state.capitalize() + (" · draft" if pr.get("draft") else ""))
        return embed

    async def _is_manager(self, user: discord.abc.User, guild: discord.Guild) -> bool:
        if await self.bot.is_owner(user):
            return True
        member = guild.get_member(user.id)
        if member is None:
            return False
        return member.guild_permissions.manage_guild or await is_admin_or_superior(self.bot, member)

    async def _managed_guilds(self, ctx: commands.Context) -> List[discord.Guild]:
        """The guild the command ran in, or in a DM every guild the user manages."""
        if ctx.guild:
            return [ctx.guild]
        return [g for g in self.bot.guilds if await self._is_manager(ctx.author, g)]

    async def _resolve(self, ctx: commands.Context, repo: str) -> Optional[Tuple[discord.Guild, str]]:
        """Find which guild tracks `repo`, replying with an error when it is none or ambiguous."""
        matches = []
        for guild in await self._managed_guilds(ctx):
            repos = await self.config.guild(guild).repos()
            key = next(
                (k for k, st in repos.items() if repo.lower() in (k.lower(), (st.get("alias") or "").lower())),
                None,
            )
            if key:
                matches.append((guild, key))
        if not matches:
            await ctx.send("That repo isn't tracked here.")
        elif len(matches) > 1:
            await ctx.send("That repo is tracked in several servers. Run this command in the server instead.")
        else:
            return matches[0]
        return None

    async def _group_check(ctx: commands.Context) -> bool:
        cog = ctx.cog
        if ctx.guild:
            return await cog._is_manager(ctx.author, ctx.guild)
        return bool(await cog._managed_guilds(ctx))

    @commands.group(name="githubfeed", aliases=["ghfeed"])
    @commands.check(_group_check)
    async def githubfeed(self, ctx: commands.Context) -> None:
        """Manage GitHub feeds. Works in servers and in DMs."""

    @staticmethod
    def _parse_repo(text: str) -> Optional[str]:
        """Accept `owner/repo` or a github.com link (with optional .git, trailing path, or <>)."""
        text = text.strip("<>")
        match = GITHUB_URL.match(text)
        if match:
            owner, name = match.groups()
            return f"{owner}/{name[:-4] if name.endswith('.git') else name}"
        return text if text.count("/") == 1 and " " not in text else None

    @staticmethod
    def _parse_events(events: Tuple[str, ...]) -> Optional[List[str]]:
        chosen = [e.lower() for e in events] or list(EVENTS)
        aliases = {"releases": "release", "commits": "commit", "prs": "pr", "pull": "pr"}
        chosen = [aliases.get(e, e) for e in chosen]
        if any(e not in EVENTS for e in chosen):
            return None
        return list(dict.fromkeys(chosen))

    @githubfeed.command(name="add")
    async def gf_add(
        self,
        ctx: commands.Context,
        repo: str,
        channel: Optional[discord.TextChannel] = None,
        *events: str,
    ) -> None:
        """Track a repo: `[p]ghfeed add owner/repo [#channel] [release commit pr]`.

        Events default to all three. In a DM the channel is required.
        Private repos need a token: `[p]ghfeed token` (the bot owner pastes it in a DM)
        """
        repo = self._parse_repo(repo)
        if repo is None:
            await ctx.send("Use `owner/repo` or a GitHub link like `https://github.com/owner/repo`.")
            return
        chosen = self._parse_events(events)
        if chosen is None:
            await ctx.send(f"Events must be any of: {', '.join(EVENTS)}.")
            return
        if channel is None:
            if ctx.guild is None:
                await ctx.send("In a DM, say which channel to post in: `add owner/repo #channel` (or a channel ID).")
                return
            channel = ctx.channel
        guild = channel.guild
        if not await self._is_manager(ctx.author, guild):
            await ctx.send("You need Manage Server in that channel's server.")
            return
        async with ctx.typing():
            status, data = await self._get(f"/repos/{repo}", use_etag=False)
        if status != 200:
            if status == 401:
                msg = "GitHub rejected the stored token (expired or invalid). The bot owner can run `ghfeed cleartoken` or `ghfeed token`."
            elif status in (403, 404):
                msg = "Repo not found, or the token lacks access to it."
            else:
                msg = f"GitHub returned HTTP {status}."
            await ctx.send(msg)
            return
        repo = data["full_name"]
        async with self.config.guild(guild).repos() as repos:
            repos[repo] = {"channel_id": channel.id, "events": chosen, "branch": None}
        await ctx.send(
            f"Tracking **{repo}** ({', '.join(chosen)}) in {channel.mention}. "
            "Existing history is not posted; only new activity from now on."
        )
        await self._poll_baseline(guild, repo)

    async def _poll_baseline(self, guild: discord.Guild, repo: str) -> None:
        try:
            await self._poll_repo(guild, repo)
        except Exception:
            log.exception("Baseline poll failed for %s", repo)

    @githubfeed.command(name="remove")
    async def gf_remove(self, ctx: commands.Context, repo: str) -> None:
        """Stop tracking a repo."""
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        async with self.config.guild(guild).repos() as repos:
            repos.pop(key, None)
        await ctx.send(f"Stopped tracking **{key}**.")

    @githubfeed.command(name="list")
    async def gf_list(self, ctx: commands.Context) -> None:
        """List tracked repos."""
        lines = []
        for guild in await self._managed_guilds(ctx):
            for name, st in (await self.config.guild(guild).repos()).items():
                chan = guild.get_channel(st["channel_id"])
                where = chan.mention if chan else "deleted channel"
                prefix = f"[{guild.name}] " if ctx.guild is None else ""
                extras = ", ".join(
                    ([f"alias {st['alias']}"] if st.get("alias") else [])
                    + (["links off"] if not st.get("links", True) else [])
                    + (["privacy on"] if st.get("privacy") else [])
                    + ([f"branch {st['branch']}"] if st.get("branch") else [])
                )
                lines.append(
                    f"{prefix}**{name}** → {where} ({', '.join(st['events'])}{'; ' + extras if extras else ''})"
                )
        await ctx.send("\n".join(lines) if lines else "No repos are tracked.")

    @githubfeed.command(name="channel")
    async def gf_channel(self, ctx: commands.Context, repo: str, channel: discord.TextChannel) -> None:
        """Change the channel a repo posts to."""
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        if channel.guild != guild:
            await ctx.send("That channel is in a different server than the repo's feed.")
            return
        async with self.config.guild(guild).repos() as repos:
            repos[key]["channel_id"] = channel.id
        await ctx.send(f"**{key}** now posts to {channel.mention}.")

    @githubfeed.command(name="events")
    async def gf_events(self, ctx: commands.Context, repo: str, *events: str) -> None:
        """Change which events a repo posts: release, commit, pr."""
        chosen = self._parse_events(events)
        if chosen is None or not events:
            await ctx.send(f"Give one or more of: {', '.join(EVENTS)}.")
            return
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        async with self.config.guild(guild).repos() as repos:
            repos[key]["events"] = chosen
            for event, marker in (("release", "last_release_id"), ("commit", "last_commit_sha"), ("pr", "pr_states")):
                if event not in chosen:
                    repos[key].pop(marker, None)
        await ctx.send(f"**{key}** now posts: {', '.join(chosen)}.")

    @githubfeed.command(name="branch")
    async def gf_branch(self, ctx: commands.Context, repo: str, branch: Optional[str] = None) -> None:
        """Limit commit posts to a branch (omit the branch for the default)."""
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        async with self.config.guild(guild).repos() as repos:
            repos[key]["branch"] = branch
            repos[key].pop("last_commit_sha", None)
        await ctx.send(f"**{key}** commits now follow `{branch or 'the default branch'}`.")

    @githubfeed.command(name="alias")
    async def gf_alias(self, ctx: commands.Context, repo: str, *, alias: Optional[str] = None) -> None:
        """Show a nicer name for a repo in posts. Omit the alias to clear it.

        The alias can also be used in place of `owner/repo` in other commands.
        """
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        if alias is not None:
            alias = alias.strip()
            if not alias or len(alias) > 50:
                await ctx.send("The alias must be 1-50 characters.")
                return
        async with self.config.guild(guild).repos() as repos:
            taken = alias and any(
                k != key and alias.lower() in (k.lower(), (st.get("alias") or "").lower())
                for k, st in repos.items()
            )
            if taken:
                await ctx.send("That name is already used by another tracked repo.")
                return
            if alias:
                repos[key]["alias"] = alias
            else:
                repos[key].pop("alias", None)
        await ctx.send(f"**{key}** will be shown as **{alias}**." if alias else f"Alias cleared for **{key}**.")

    @githubfeed.command(name="links")
    async def gf_links(self, ctx: commands.Context, repo: str, setting: bool) -> None:
        """Turn clickable links on or off in a repo's posts: `[p]ghfeed links <repo> on|off`."""
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        async with self.config.guild(guild).repos() as repos:
            repos[key]["links"] = setting
        await ctx.send(f"Links in **{key}** posts are now {'on' if setting else 'off'}.")

    @githubfeed.command(name="privacy")
    async def gf_privacy(self, ctx: commands.Context, repo: str, setting: bool) -> None:
        """Hide the commit hash and link in commit posts: `[p]ghfeed privacy <repo> on|off`."""
        found = await self._resolve(ctx, repo)
        if found is None:
            return
        guild, key = found
        async with self.config.guild(guild).repos() as repos:
            repos[key]["privacy"] = setting
        await ctx.send(f"Privacy mode for **{key}** is now {'on' if setting else 'off'}.")

    @githubfeed.command(name="token")
    @commands.is_owner()
    async def gf_token(self, ctx: commands.Context) -> None:
        """Set the GitHub token. The bot DMs you; paste the token there, never in a server."""
        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                "Reply with your GitHub token within 2 minutes (or `cancel`). "
                "It is used for private repos and higher rate limits."
            )
        except discord.Forbidden:
            await ctx.send("I can't DM you. Enable DMs from server members and try again.")
            return
        if ctx.guild:
            await ctx.send("Check your DMs.")

        def check(m: discord.Message) -> bool:
            return m.author.id == ctx.author.id and m.channel.id == dm.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=120)
        except asyncio.TimeoutError:
            await dm.send("Timed out. Nothing was saved.")
            return
        token = msg.content.strip()
        # Discord only lets bots delete their own messages in DMs, so this normally fails;
        # it is attempted anyway in case that ever changes.
        deleted = True
        try:
            await msg.delete()
        except discord.HTTPException:
            deleted = False
        if token.lower() == "cancel":
            await dm.send("Cancelled.")
            return
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Red-GithubFeed",
            "Authorization": f"Bearer {token}",
        }
        async with self.session.get(f"{API}/rate_limit", headers=headers) as resp:
            valid = resp.status == 200
        if not valid:
            await dm.send("GitHub rejected that token. Nothing was saved.")
        else:
            await self.bot.set_shared_api_tokens("github", token=token)
            self._etags.clear()
            await dm.send("Token saved.")
        if not deleted:
            await dm.send(
                "I can't delete your message in a DM. Please delete the one containing your token yourself "
                "(right-click, Delete)."
            )

    @githubfeed.command(name="cleartoken")
    @commands.is_owner()
    async def gf_cleartoken(self, ctx: commands.Context) -> None:
        """Remove the stored GitHub token."""
        await self.bot.remove_shared_api_tokens("github", "token")
        self._etags.clear()
        await ctx.send("GitHub token removed.")

    @githubfeed.command(name="interval")
    @commands.is_owner()
    async def gf_interval(self, ctx: commands.Context, seconds: int) -> None:
        """Set the polling interval in seconds (minimum 60)."""
        if seconds < MIN_INTERVAL:
            await ctx.send(f"The minimum interval is {MIN_INTERVAL} seconds.")
            return
        await self.config.interval.set(seconds)
        self.poll.change_interval(seconds=seconds)
        await ctx.send(f"Polling every {seconds} seconds.")

    @githubfeed.command(name="check")
    async def gf_check(self, ctx: commands.Context) -> None:
        """Poll GitHub now."""
        async with ctx.typing():
            posted = await self._poll_all(await self._managed_guilds(ctx))
        await ctx.send(f"Check complete: {posted} update(s) posted.")
