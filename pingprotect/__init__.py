from .pingprotect import PingProtect


async def setup(bot):
    await bot.add_cog(PingProtect(bot))
