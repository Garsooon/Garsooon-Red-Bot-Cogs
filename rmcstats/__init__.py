from .rmcstats import RMCStats


async def setup(bot):
    await bot.add_cog(RMCStats(bot))
