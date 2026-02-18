# Paracord

<p align="center">
  <img src="logo.svg" alt="Paracord logo" width="200">
</p>

CLI tool for bulk-deleting your Discord messages across servers, DMs, and group DMs.

Built for privacy-conscious users who want to clean up their Discord history. Handles 100K+ messages unattended with progress persistence and automatic rate limit handling.

## Why Not Undiscord?

[Undiscord](https://github.com/victornpb/undiscord) is a great browser-based tool, but it has a fundamental limitation: it uses **offset-based pagination**, which hits Discord's hard ceiling at ~9,975 results. If you have more than ~10,000 messages in a channel, undiscord simply cannot reach them.

Paracord uses **cursor-based pagination** (`max_id` as a sliding cursor) to walk backward through your entire message history with no ceiling. It also runs as a standalone CLI tool -- no browser required, no browser tab to keep open for hours.

| Feature | Undiscord | Paracord |
|---------|-----------|----------|
| Max messages per channel | ~10,000 (offset ceiling) | **Unlimited** (cursor-based) |
| Runtime environment | Browser userscript | Python CLI |
| Multi-channel batch | Manual, one at a time | Config-driven, fully unattended |
| Progress persistence | None (lose progress if tab closes) | JSON checkpoint with `--resume` |
| Ghost message handling | Counts as failure | Detected and skipped (no delay wasted) |
| Signal handling | None | Graceful Ctrl+C with progress save |
| Content overwrite (Meow mode) | No | Edit messages before/instead of deleting |

## How It Works

Paracord uses Discord's undocumented search API to find your messages, then deletes them one by one via the delete API. The key innovation is the pagination strategy:

1. **Search** for your messages sorted by timestamp (newest first)
2. **Delete** each message in the batch
3. **Advance cursor** by setting `max_id` to the oldest message ID minus one
4. **Repeat** from the new cursor position until no messages remain

This cursor approach means offset stays at 0 and never hits Discord's ceiling.

## Warning

This tool uses Discord's private API with a user token. This violates Discord's Terms of Service. Potential consequences include account warnings, suspension, or permanent ban. Use at your own risk for legitimate privacy purposes.

## Requirements

- Python 3.9+
- `requests` library
- A Discord user authentication token

## Quick Start

```bash
# Clone the repo
git clone https://github.com/daiimus/paracord.git
cd paracord

# Install dependencies
pip install -r requirements.txt

# Set up your token
cp .env.example .env
# Edit .env and paste your Discord token

# Discover your servers and create config
python3 paracord.py --discover

# Preview what would be deleted (no actual deletions)
python3 paracord.py --config config.json --dry-run

# Execute deletion
python3 paracord.py --config config.json
```

## Getting Your Discord Token

You need your Discord **user token** (not a bot token). There are several ways to obtain it:

### From Browser DevTools
1. Open Discord in your browser (discord.com/app)
2. Open DevTools (F12 or Cmd+Option+I)
3. Go to the **Network** tab
4. Send a message or perform any action in Discord
5. Click on any request to `discord.com/api`
6. In the request headers, find `Authorization:` -- that's your token

### From a Terminal Discord Client
If you use a terminal client like [endcord](https://github.com/sparklost/endcord) or [discordo](https://github.com/ayn2op/discordo), your token is typically stored in their config files.

### Security Notes
- Your token provides **full access** to your Discord account
- Never share it with anyone or commit it to version control
- After you're done, change your Discord password to invalidate the token
- The `.env` file is gitignored by default

## Usage

### Discovery Mode

Scan your Discord account and generate a config file with all your servers, DMs, and group DMs:

```bash
python3 paracord.py --discover
```

This walks you through selecting which servers and DMs to include. The result is a `config.json` file ready for batch processing.

### Dry Run

Preview what would be deleted without actually deleting anything:

```bash
python3 paracord.py --config config.json --dry-run
```

### Execute

Start the deletion process:

```bash
python3 paracord.py --config config.json
```

You'll be asked to confirm before any deletion begins. Use `--yes` to skip the confirmation prompt (useful for unattended runs).

### Resume

If the script is interrupted (Ctrl+C, network failure, etc.), resume from the last checkpoint:

```bash
python3 paracord.py --config config.json --resume
```

Progress is saved to `.paracord_progress.json` after each target completes.

### Running Unattended

For long-running deletions, use `screen` or `tmux`:

```bash
# Start a screen session
screen -S paracord

# Run the script
python3 paracord.py --config config.json --yes

# Detach: Ctrl+A then D
# Reattach later: screen -r paracord
```

### All Options

```
python3 paracord.py --help

Options:
  --version, -V         Show version
  --token, -t TOKEN     Discord auth token (overrides .env)
  --config, -c FILE     Path to config.json
  --discover, -d        Discover servers and create config
  --dry-run             Preview without deleting
  --resume, -r          Resume from saved progress
  --verify-auth         Validate token and exit
  --yes, -y             Skip confirmation prompt
  --meow [MODE]         Meow mode (default: edit_and_delete, or edit_only)
  --skip-meowed         Skip meowed messages during deletion
```

## Configuration

The config file is generated by `--discover` mode, but you can also create or edit it manually. See `config_template.json` for the full format.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `search_delay` | 10 | Seconds between search API calls |
| `delete_delay` | 1 | Seconds between delete API calls |
| `skip_pinned` | true | Skip pinned messages |
| `skip_meowed` | false | Skip meowed messages during deletion |
| `max_retries` | 3 | Retry attempts for failed deletions |
| `dry_run` | false | Preview mode |
| `meow_mode` | "off" | `off`, `edit_and_delete`, or `edit_only` (see Meow Mode) |

### Delay Tuning

The defaults (10s search, 1s delete) have been tested over multiple 50+ hour runs deleting 100K+ messages without triggering account action. If you want to be more cautious:

| Profile | search_delay | delete_delay | Notes |
|---------|-------------|-------------|-------|
| **Default** | 10 | 1 | Battle-tested over 136K deletions |
| Conservative | 30 | 3 | Slower but extra cautious |
| Aggressive | 5 | 0.5 | Faster, higher risk of rate limits |

The script handles rate limits automatically with dynamic backoff (waits 2x Discord's `retry_after` value), so even if you hit limits, it recovers gracefully.

### Target Types

```json
{
  "type": "guild",
  "guild_id": "123456789012345678",
  "guild_name": "Server Name",
  "channel_id": "123456789012345678",
  "channel_name": "general"
}
```

```json
{
  "type": "dm",
  "channel_id": "123456789012345678",
  "recipient_name": "Username"
}
```

```json
{
  "type": "group_dm",
  "channel_id": "123456789012345678",
  "group_name": "Group Name"
}
```

Add `"enabled": false` to any target to skip it without removing it from the config.

## Ghost Messages

After deleting messages, Discord's search index takes time to update. During subsequent passes, the search API may return messages that have already been deleted. These are "ghost" entries.

Paracord detects ghosts (404 response on delete) and skips the normal delete delay for them, since no actual API work was done. In a cleanup with 100K+ deletions, ghost-heavy channels are common on follow-up passes.

## Meow Mode

Meow mode overwrites every message's content with a bold, multi-line meow before (optionally) deleting it:

```
**MEOW, MEOW.**
**MEOW, MEOW.**
**MEOW, MEOW.**
**MEOW, MEOW.**
```

This is useful as a belt-and-suspenders approach: even if a deletion fails or you choose to leave messages standing, the original text is gone.

Each message also gets a random mouse emoji reaction (🐭 or 🐁) before being edited, leaving a visible tag even on messages that were already meowed.

### Modes

| Mode | Behavior |
|------|----------|
| `off` (default) | Normal deletion, no editing |
| `edit_and_delete` | Edit message to meow text, then delete it |
| `edit_only` | Edit message to meow text and leave it standing |

### Usage

```bash
# Edit then delete (default meow behavior)
python3 paracord.py --config config.json --meow

# Edit only -- leave meowed messages in place
python3 paracord.py --config config.json --meow edit_only
```

Or set it in your config.json:

```json
"settings": {
  "meow_mode": "edit_and_delete"
}
```

The `--meow` CLI flag overrides whatever is in the config file. Messages that are already meowed are skipped on subsequent passes.

**Note:** Meow mode adds extra API calls per message (react + PATCH, plus DELETE if not edit_only), so expect longer runtimes compared to normal deletion. The same rate limit handling applies.

### Preserving Meowed Messages

If you used `edit_only` mode to leave meowed messages standing and later want to delete the remaining un-meowed messages while keeping the meows, use `--skip-meowed`:

```bash
# Step 1: Meow specific channels
python3 paracord.py --config favorites.json --meow edit_only

# Step 2: Delete everything except meowed messages
python3 paracord.py --config all_channels.json --skip-meowed
```

Or set it in your config:

```json
"settings": {
  "skip_meowed": true
}
```

The `--skip-meowed` CLI flag overrides the config setting. Messages are matched by exact content against the meow format (`**MEOW, MEOW.**` x4 lines).

## Time Estimates

Rough estimates based on default settings (10s search, 1s delete):

| Messages | Estimated Time |
|----------|----------------|
| 1,000 | ~30 minutes |
| 5,000 | ~2 hours |
| 10,000 | ~4 hours |
| 50,000 | ~20 hours |
| 100,000+ | ~40-50 hours |

Actual time depends on message density per channel, rate limit frequency, and ghost message ratio on subsequent passes.

## Project Structure

```
paracord/
├── paracord.py           # Main script (~1050 lines)
├── config_template.json  # Example configuration
├── .env.example          # Token template
├── .gitignore            # Excludes secrets, logs, config
├── requirements.txt      # Python dependencies
├── UNLICENSE             # Public domain
└── README.md             # This file
```

Generated at runtime (gitignored):
- `config.json` - Your target configuration (contains server/channel IDs)
- `.paracord_progress.json` - Resume checkpoint
- `paracord.log` - Detailed execution log

## After You're Done

1. **Change your Discord password** -- this invalidates your token
2. **Delete your `.env` file** -- `rm .env`
3. **Delete your config** -- `rm config.json` (contains your server/channel IDs)
4. **Review the log** -- check `paracord.log` for any failures
5. **Verify in Discord** -- manually check a few servers/DMs to confirm

## Credits

Inspired by [undiscord](https://github.com/victornpb/undiscord) by victornpb. Built from scratch in Python with cursor-based pagination to handle large-scale deletions that offset-based tools cannot reach.

## License

This is free and unencumbered software released into the public domain. See [UNLICENSE](UNLICENSE) for details.
