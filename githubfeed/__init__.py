from .githubfeed import GithubFeed


async def setup(bot):
    await bot.add_cog(GithubFeed(bot))
