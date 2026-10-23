# RMCStats

A Red-DiscordBot cog that shows RetroMC player statistics and leaderboards from the RetroMC statistics API (`api.retromc.org`).

## Install

```
[p]repo add Garsooon-Red-Bot-Cogs https://github.com/Garsooon/Garsooon-Red-Bot-Cogs
[p]cog install Garsooon-Red-Bot-Cogs rmcstats
[p]load rmcstats
```

Requires Red 3.5+. No configuration needed.

## Commands

Both are hybrid commands, so they also work as slash commands.

| Command                         | Purpose                                                                |
|---------------------------------|------------------------------------------------------------------------|
| `[p]stats <player>`             | Show a player's stats (username or UUID)                               |
| `[p]leaderboard [stat] [limit]` | Show a top list; `stat` defaults to `playTime`, `limit` to 10 (max 25) |

Leaderboard stats: `playTime`, `joinCount`, `creaturesKilled`, `playersKilled`, `blocksPlaced`, `blocksDestroyed`, `metersTraveled`, `playerDeaths`, `itemsDropped`, `trustScore`. Short aliases also work, for example `deaths`, `pvp`, `mobs`, `distance`, `trust` and `joins`.
