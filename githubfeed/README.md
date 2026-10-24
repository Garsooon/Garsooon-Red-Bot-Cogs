# GithubFeed

A Red-DiscordBot cog that posts new GitHub **releases**, **commits** and **pull requests** to a Discord channel. It polls the GitHub REST API, so no webhook or public server is needed.

## Install

```
[p]repo add Garsooon-Red-Bot-Cogs https://github.com/Garsooon/Garsooon-Red-Bot-Cogs
[p]cog install Garsooon-Red-Bot-Cogs githubfeed
[p]load githubfeed
```

Requires Red 3.5+.

## Private repos / rate limits

Create a GitHub token, then run this as the bot owner:

```
[p]ghfeed token
```

The bot DMs you and waits for you to reply with the token there. The token is never accepted in a server channel. It is checked against GitHub before it is saved. Discord does not let bots delete a user's DM messages, so the bot asks you to delete your reply yourself. `[p]ghfeed cleartoken` removes the stored token.

- Classic PAT: `repo` scope.
- Fine-grained PAT: select the repos and grant read-only **Contents**, **Pull requests** and **Metadata**.

Without a token only public repos work, at 60 requests per hour.

## Commands (`[p]githubfeed`, alias `[p]ghfeed`)

| Command                                           | Purpose                                                |
|---------------------------------------------------|--------------------------------------------------------|
| `add <owner/repo or GitHub link> [#channel] [release commit pr]` | Track a repo (defaults to all events, current channel) |
| `remove <owner/repo>`                             | Stop tracking                                          |
| `list`                                            | Show tracked repos                                     |
| `channel <owner/repo> <#channel>`                 | Change target channel                                  |
| `events <owner/repo> <events...>`                 | Change which events post                               |
| `branch <owner/repo> [branch]`                    | Follow commits on one branch                           |
| `alias <repo> [name]`                             | Show a nicer name in posts (omit to clear); usable in place of `owner/repo` |
| `links <repo> on\|off`                            | Turn clickable links on or off for releases and PRs    |
| `privacy <repo> on\|off`                         | Hide the commit hash and link in commit posts          |
| `token` / `cleartoken`                            | Bot owner: set (via DM) or remove the GitHub token     |
| `interval <seconds>`                              | Bot owner: polling interval (default 300, min 60)      |
| `check`                                           | Poll immediately                                       |

Adding a repo records its current state as a baseline, so old history is never posted.

All commands also work in a DM with the bot. In a DM you only see and manage servers where you have Manage Server (bot owners see all), and `add` needs the target channel (mention or ID), e.g. `add owner/repo 123456789012345678`. If the same repo is tracked in several servers, run the command in the server instead.
