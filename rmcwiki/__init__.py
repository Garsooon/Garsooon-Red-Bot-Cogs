from .rmcwiki import RMCWiki


async def setup(bot):
    await bot.add_cog(RMCWiki(bot))
