import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_list, pagify

VALID_ACTIONS = ("delete", "warn", "both")


class PingProtect(commands.Cog):
    """Create groups of users who cannot be pinged.

    Build named protection groups, add members to them, and the bot will
    stop other people from mentioning those members. You can choose whether
    a violating message is deleted, warned about, or both, and you can
    exempt specific roles or users from the protection.
    """

    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=0x50494E4750524F54, force_registration=True
        )
        self.config.register_guild(
            groups={},
            bypass_roles=[],
            bypass_users=[],
            warn_message="{author}, please do not ping {target} — they are ping-protected.",
        )

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre = super().format_help_for_context(ctx)
        return f"{pre}\n\nVersion: {self.__version__}"

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Remove a user from every protection group in every guild."""
        all_guilds = await self.config.all_guilds()
        for guild_id in all_guilds:
            guild_conf = self.config.guild_from_id(guild_id)
            async with guild_conf.groups() as groups:
                for data in groups.values():
                    if user_id in data.get("members", []):
                        data["members"].remove(user_id)
            async with guild_conf.bypass_users() as users:
                if user_id in users:
                    users.remove(user_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if not message.mentions:
            return

        conf = self.config.guild(message.guild)
        groups = await conf.groups()
        if not groups:
            return

        author = message.author
        if isinstance(author, discord.Member) and author.guild_permissions.manage_messages:
            return

        bypass_roles = set(await conf.bypass_roles())
        if isinstance(author, discord.Member) and any(r.id in bypass_roles for r in author.roles):
            return
        if author.id in await conf.bypass_users():
            return

        mentioned_ids = {m.id for m in message.mentions}

        protected_hits = set()
        wants_delete = False
        wants_warn = False
        for data in groups.values():
            members = data.get("members", [])
            hit = mentioned_ids.intersection(members)
            if not hit:
                continue
            hit.discard(author.id)
            if not hit:
                continue
            protected_hits.update(hit)
            act = data.get("action", "delete")
            if act in ("delete", "both"):
                wants_delete = True
            if act in ("warn", "both"):
                wants_warn = True

        if not protected_hits:
            return

        if wants_delete:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

        if wants_warn:
            targets = []
            for uid in protected_hits:
                member = message.guild.get_member(uid)
                targets.append(member.display_name if member else str(uid))
            template = await conf.warn_message()
            text = template.format(
                author=author.mention,
                target=humanize_list(targets) if targets else "that user",
            )
            try:
                await message.channel.send(
                    text,
                    delete_after=10,
                    allowed_mentions=discord.AllowedMentions(
                        users=[author], roles=False, everyone=False
                    ),
                )
            except discord.Forbidden:
                pass

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(aliases=["pp"])
    async def pingprotect(self, ctx: commands.Context):
        """Manage ping-protection groups."""

    @pingprotect.command(name="create")
    async def pp_create(self, ctx: commands.Context, name: str):
        """Create a new protection group."""
        name = name.lower()
        async with self.config.guild(ctx.guild).groups() as groups:
            if name in groups:
                return await ctx.send(f"A group named `{name}` already exists.")
            groups[name] = {"members": [], "action": "delete"}
        await ctx.send(
            f"Created protection group `{name}`. Add members with "
            f"`{ctx.clean_prefix}pingprotect add {name} <member>`."
        )

    @pingprotect.command(name="delete", aliases=["remove_group", "del"])
    async def pp_delete(self, ctx: commands.Context, name: str):
        """Delete a protection group entirely."""
        name = name.lower()
        async with self.config.guild(ctx.guild).groups() as groups:
            if name not in groups:
                return await ctx.send(f"No group named `{name}`.")
            del groups[name]
        await ctx.send(f"Deleted protection group `{name}`.")

    @pingprotect.command(name="add")
    async def pp_add(self, ctx: commands.Context, name: str, members: commands.Greedy[discord.Member]):
        """Add one or more members to a protection group."""
        name = name.lower()
        if not members:
            return await ctx.send("You need to specify at least one member.")
        async with self.config.guild(ctx.guild).groups() as groups:
            if name not in groups:
                return await ctx.send(f"No group named `{name}`. Create it first.")
            added = []
            for member in members:
                if member.id not in groups[name]["members"]:
                    groups[name]["members"].append(member.id)
                    added.append(member.display_name)
        if added:
            await ctx.send(f"Added to `{name}`: {humanize_list(added)}.")
        else:
            await ctx.send("Those members are already in that group.")

    @pingprotect.command(name="removemember", aliases=["rm"])
    async def pp_removemember(self, ctx: commands.Context, name: str, members: commands.Greedy[discord.Member]):
        """Remove one or more members from a protection group."""
        name = name.lower()
        if not members:
            return await ctx.send("You need to specify at least one member.")
        async with self.config.guild(ctx.guild).groups() as groups:
            if name not in groups:
                return await ctx.send(f"No group named `{name}`.")
            removed = []
            for member in members:
                if member.id in groups[name]["members"]:
                    groups[name]["members"].remove(member.id)
                    removed.append(member.display_name)
        if removed:
            await ctx.send(f"Removed from `{name}`: {humanize_list(removed)}.")
        else:
            await ctx.send("Those members were not in that group.")

    @pingprotect.command(name="action")
    async def pp_action(self, ctx: commands.Context, name: str, action: str):
        """Set what happens on a violation: delete, warn, or both."""
        name = name.lower()
        action = action.lower()
        if action not in VALID_ACTIONS:
            return await ctx.send(f"Action must be one of: {humanize_list(VALID_ACTIONS)}.")
        async with self.config.guild(ctx.guild).groups() as groups:
            if name not in groups:
                return await ctx.send(f"No group named `{name}`.")
            groups[name]["action"] = action
        await ctx.send(f"Group `{name}` will now **{action}** on a violation.")

    @pingprotect.command(name="list")
    async def pp_list(self, ctx: commands.Context):
        """List all protection groups in this server."""
        groups = await self.config.guild(ctx.guild).groups()
        if not groups:
            return await ctx.send("There are no protection groups yet.")
        lines = []
        for name, data in sorted(groups.items()):
            count = len(data.get("members", []))
            action = data.get("action", "delete")
            lines.append(f"{name} — {count} member(s), action: {action}")
        for page in pagify("\n".join(lines)):
            await ctx.send(box(page, lang="ini"))

    @pingprotect.command(name="show", aliases=["info"])
    async def pp_show(self, ctx: commands.Context, name: str):
        """Show the members of a protection group."""
        name = name.lower()
        groups = await self.config.guild(ctx.guild).groups()
        if name not in groups:
            return await ctx.send(f"No group named `{name}`.")
        data = groups[name]
        names = []
        for uid in data.get("members", []):
            member = ctx.guild.get_member(uid)
            names.append(member.display_name if member else f"<left server: {uid}>")
        embed = discord.Embed(
            title=f"Protection group: {name}",
            description=humanize_list(names) if names else "*No members yet.*",
            color=await ctx.embed_color(),
        )
        embed.add_field(name="Action", value=data.get("action", "delete"))
        embed.add_field(name="Members", value=str(len(names)))
        await ctx.send(embed=embed)

    @pingprotect.group(name="bypass")
    async def pp_bypass(self, ctx: commands.Context):
        """Manage who is exempt from ping protection.

        Members with the Manage Messages permission are always exempt.
        """

    @pp_bypass.command(name="addrole")
    async def bypass_addrole(self, ctx: commands.Context, role: discord.Role):
        """Let a role ping protected members freely."""
        async with self.config.guild(ctx.guild).bypass_roles() as roles:
            if role.id in roles:
                return await ctx.send("That role already bypasses protection.")
            roles.append(role.id)
        await ctx.send(f"`{role.name}` can now ping protected members.")

    @pp_bypass.command(name="removerole")
    async def bypass_removerole(self, ctx: commands.Context, role: discord.Role):
        """Stop a role from bypassing protection."""
        async with self.config.guild(ctx.guild).bypass_roles() as roles:
            if role.id not in roles:
                return await ctx.send("That role was not bypassing protection.")
            roles.remove(role.id)
        await ctx.send(f"`{role.name}` no longer bypasses protection.")

    @pp_bypass.command(name="adduser")
    async def bypass_adduser(self, ctx: commands.Context, member: discord.Member):
        """Let a specific user ping protected members freely."""
        async with self.config.guild(ctx.guild).bypass_users() as users:
            if member.id in users:
                return await ctx.send("That user already bypasses protection.")
            users.append(member.id)
        await ctx.send(f"{member.display_name} can now ping protected members.")

    @pp_bypass.command(name="removeuser")
    async def bypass_removeuser(self, ctx: commands.Context, member: discord.Member):
        """Stop a specific user from bypassing protection."""
        async with self.config.guild(ctx.guild).bypass_users() as users:
            if member.id not in users:
                return await ctx.send("That user was not bypassing protection.")
            users.remove(member.id)
        await ctx.send(f"{member.display_name} no longer bypasses protection.")

    @pp_bypass.command(name="list")
    async def bypass_list(self, ctx: commands.Context):
        """Show configured bypass roles and users."""
        conf = self.config.guild(ctx.guild)
        role_ids = await conf.bypass_roles()
        user_ids = await conf.bypass_users()
        roles = [ctx.guild.get_role(r) for r in role_ids]
        roles = [r.name for r in roles if r]
        users = [ctx.guild.get_member(u) for u in user_ids]
        users = [u.display_name for u in users if u]
        embed = discord.Embed(title="Bypass configuration", color=await ctx.embed_color())
        embed.add_field(
            name="Roles", value=humanize_list(roles) if roles else "*None*", inline=False
        )
        embed.add_field(
            name="Users", value=humanize_list(users) if users else "*None*", inline=False
        )
        await ctx.send(embed=embed)

    @pingprotect.command(name="warnmessage", aliases=["setwarn"])
    async def pp_warnmessage(self, ctx: commands.Context, *, message: str = None):
        """Set the warning message. Use {author} and {target} as placeholders.

        Run without arguments to see the current message.
        """
        conf = self.config.guild(ctx.guild)
        if message is None:
            current = await conf.warn_message()
            return await ctx.send(box(current))
        try:
            message.format(author="@user", target="@target")
        except (KeyError, IndexError):
            return await ctx.send(
                "Invalid placeholders. Only `{author}` and `{target}` are allowed."
            )
        await conf.warn_message.set(message)
        await ctx.send("Warning message updated.")
