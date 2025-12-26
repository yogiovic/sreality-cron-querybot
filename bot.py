import os
import json
import re
import datetime
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import asyncio
from scraper import scrape_all_pages
import requests
from aiohttp import web

# --- Setup ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID'))
COMMAND_CHANNEL_ID = int(os.getenv('COMMAND_CHANNEL_ID'))
WATCHDOGS_FILE = 'watchdogs.json'

intents = discord.Intents.default()
intents.guilds = True  # we only need guilds for slash commands + channels/webhooks

bot = commands.Bot(command_prefix='!', intents=intents)
# Use the built-in application command tree from the bot
tree = bot.tree

# --- Watchdog Persistence ---
DEFAULT_INTERVAL_MINUTES = 12 * 60  # 12 hours


def load_watchdogs():
    if not os.path.exists(WATCHDOGS_FILE):
        return []
    with open(WATCHDOGS_FILE, 'r') as f:
        data = json.load(f)
    # Backwards-compatible normalization
    for w in data:
        if 'interval_minutes' not in w:
            w['interval_minutes'] = DEFAULT_INTERVAL_MINUTES
        if 'created_at' not in w:
            w['created_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
        if 'created_by' not in w:
            w['created_by'] = None
        if 'last_check' not in w:
            w['last_check'] = None
    return data


def save_watchdogs(watchdogs):
    with open(WATCHDOGS_FILE, 'w') as f:
        json.dump(watchdogs, f, indent=2)


def find_watchdog_by_channel(watchdogs, channel_id: int):
    for w in watchdogs:
        if w.get('channel_id') == channel_id:
            return w
    return None


def find_watchdog_by_url(watchdogs, url: str):
    for w in watchdogs:
        if w.get('url') == url:
            return w
    return None


# --- Helper Functions ---
def slugify(url: str) -> str:
    """Create a simple, valid Discord channel name from a Sreality URL.

    Pattern: YYYYMMDD-hledani-<first-two-path-segments>
      e.g. '20251203-hledani-pronajem-byty'

    The user can always rename the channel later in Discord.
    """
    today = datetime.date.today().strftime('%Y%m%d')

    if not url:
        return f"{today}-sreality-watchdog"

    # Drop scheme
    base = url.split('://', 1)[-1]
    # Take the path before query/fragment
    base = base.split('?', 1)[0].split('#', 1)[0]

    # Extract path segments after the domain
    parts = base.split('/', 1)
    path = ''
    if len(parts) == 2:
        path = parts[1]
    segments = [seg for seg in path.split('/') if seg]

    short_segments = []
    if segments:
        # Always start with 'hledani' if present
        if segments[0] == 'hledani':
            short_segments.append('hledani')
            short_segments.extend(segments[1:3])  # next two segments (e.g. pronajem, byty)
        else:
            short_segments.extend(segments[:2])

    if not short_segments:
        short_segments = ['sreality-watchdog']

    # Join and normalize
    tail = '-'.join(short_segments).lower()
    tail = re.sub(r'[^a-z0-9-]', '-', tail)
    tail = re.sub(r'-{2,}', '-', tail).strip('-') or 'sreality-watchdog'

    name = f"{today}-{tail}"
    # Ensure <= 80 chars
    name = name[:80].strip('-') or f"{today}-sreality-watchdog"
    return name


def format_listing_message(listing, creator_mention: str | None = None) -> str:
    name = listing.get('name', 'N/A')
    url = listing.get('listingUrl', '')
    parts = [f"**New Listing:** {name}"]
    if creator_mention:
        parts.append(creator_mention)
    if url:
        parts.append(f"<{url}>")
    return ' - '.join(parts)


def post_to_webhook(webhook_url, listings, creator_mention: str | None = None):
    """Posts new listings to a Discord webhook with a simple, consistent format."""
    if not listings:
        return

    import time

    messages = [format_listing_message(l, creator_mention) for l in listings]

    # Send messages in chunks of 10 to avoid hitting Discord rate limits
    for i in range(0, len(messages), 10):
        chunk = messages[i:i+10]
        content = "\n".join(chunk)
        try:
            response = requests.post(webhook_url, json={'content': content}, timeout=10)
            if response.status_code == 429:
                retry_after = response.json().get('retry_after', 5)
                print(f"Rate limited by Discord, waiting {retry_after}s...")
                time.sleep(retry_after)
                # Retry once
                response = requests.post(webhook_url, json={'content': content}, timeout=10)
            if response.status_code not in (200, 204):
                print(f"Webhook error {response.status_code}: {response.text}")
            else:
                print(f"Successfully posted {len(chunk)} listings to webhook")
        except Exception as e:
            print(f"Failed to post to webhook: {e}")
        time.sleep(1)  # Delay between batches to avoid rate limits


# --- Bot Commands ---
@tree.command(name="add_watchdog", description="Add a new Sreality search to watch.")
@app_commands.describe(
    url="Full Sreality search URL to watch",
    checks_per_day="How many times per day to check for new listings (default 2)",
)
async def add_watchdog(
    interaction: discord.Interaction,
    url: str,
    checks_per_day: app_commands.Range[int, 1, 96] | None = None,
):
    """Creates a channel, performs an initial deep scan, and sets up a webhook for a new Sreality URL."""
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    watchdogs = load_watchdogs()
    if find_watchdog_by_url(watchdogs, url):
        await interaction.followup.send("This URL is already being watched.")
        return

    channel_name = slugify(url)
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        await interaction.followup.send("Could not find the configured guild.")
        return

    # Determine interval from checks_per_day
    if checks_per_day is None:
        interval_minutes = DEFAULT_INTERVAL_MINUTES
    else:
        interval_minutes = max(1, int(24 * 60 / checks_per_day))

    try:
        await interaction.followup.send(
            f"Creating watchdog for `{url}` with interval ~{interval_minutes} minutes between checks. "
            "Performing initial deep scan (up to 999 pages). This may take several minutes..."
        )

        watchdog_data_dir = os.path.join('data', channel_name)
        initial_listings = scrape_all_pages(
            url,
            max_pages=999,
            save_artifacts=True,
            output_dir=watchdog_data_dir,
        )
        initial_ids = {listing.get('hash') or listing.get('id') for listing in initial_listings}

        channel = await guild.create_text_channel(channel_name)
        webhook = await channel.create_webhook(name=f"{channel_name}-updates")

        new_watchdog = {
            "name": channel_name,
            "url": url,
            "channel_id": channel.id,
            "webhook_url": webhook.url,
            "last_seen_ids": list(initial_ids),
            "interval_minutes": interval_minutes,
            "created_at": datetime.datetime.utcnow().isoformat() + 'Z',
            "created_by": interaction.user.id if interaction.user else None,
            "last_check": None,
        }
        watchdogs.append(new_watchdog)
        save_watchdogs(watchdogs)

        # Post an initial message into the newly created channel with basic info and the query URL
        info_lines = [
            f"Watchdog created by {interaction.user.mention if interaction.user else 'unknown user'}",
            f"Query URL: <{url}>",
            f"Checks roughly every {interval_minutes} minutes.",
            f"Initial scan found {len(initial_ids)} listings.",
        ]
        try:
            await channel.send("\n".join(info_lines))
        except Exception as e:
            print(f"Failed to send initial watchdog info to channel: {e}")

        await interaction.followup.send(
            f"Watchdog created in channel {channel.mention}. I will now check for new listings approximately "
            f"every {interval_minutes} minutes."
        )
    except Exception as e:
        await interaction.followup.send(f"Failed to create watchdog: {e}")


@tree.command(name="remove_watchdog", description="Remove an existing watchdog.")
@app_commands.describe(channel="Watchdog channel to remove")
async def remove_watchdog(interaction: discord.Interaction, channel: discord.TextChannel):
    """Removes a watchdog and deletes its channel."""
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    watchdogs = load_watchdogs()
    watchdog_to_remove = None
    for w in watchdogs:
        if w['channel_id'] == channel.id:
            watchdog_to_remove = w
            break

    if not watchdog_to_remove:
        await interaction.followup.send("This channel is not a watchdog channel.")
        return

    try:
        watchdogs.remove(watchdog_to_remove)
        save_watchdogs(watchdogs)

        await channel.delete()

        await interaction.followup.send(
            f"Watchdog and channel `{channel.name}` removed successfully."
        )
    except Exception as e:
        await interaction.followup.send(f"Failed to remove watchdog: {e}")


@tree.command(name="list_watchdogs", description="List all active Sreality watchdogs.")
async def list_watchdogs(interaction: discord.Interaction):
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    watchdogs = load_watchdogs()
    if not watchdogs:
        await interaction.response.send_message("There are no active watchdogs.")
        return

    lines = []
    now = datetime.datetime.utcnow()
    for w in watchdogs:
        created = w.get('created_at', 'unknown')
        interval = w.get('interval_minutes', DEFAULT_INTERVAL_MINUTES)
        last_check = w.get('last_check') or 'never'
        cid = w.get('channel_id')
        url = w.get('url')
        line = f"• <#{cid}> | every ~{interval} min | created {created} | last check {last_check} | {url}"
        lines.append(line)

    await interaction.response.send_message("Active watchdogs:\n" + "\n".join(lines))


@tree.command(name="watchdog_info", description="Show detailed info for a single watchdog.")
@app_commands.describe(channel="Watchdog channel to inspect")
async def watchdog_info(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    watchdogs = load_watchdogs()
    w = find_watchdog_by_channel(watchdogs, channel.id)
    if not w:
        await interaction.response.send_message("This channel is not a watchdog channel.")
        return

    creator_id = w.get('created_by')
    creator_mention = f"<@{creator_id}>" if creator_id else 'unknown'
    lines = [
        f"Channel: {channel.mention}",
        f"Created by: {creator_mention}",
        f"Created at: {w.get('created_at', 'unknown')}",
        f"URL: <{w.get('url')}>",
        f"Interval: {w.get('interval_minutes', DEFAULT_INTERVAL_MINUTES)} minutes",
        f"Last check: {w.get('last_check') or 'never'}",
        f"Known listings: {len(w.get('last_seen_ids', []))}",
    ]
    await interaction.response.send_message("\n".join(lines))


@tree.command(name="set_watchdog_interval", description="Change how often a watchdog is checked for new listings.")
@app_commands.describe(
    channel="Watchdog channel to configure",
    checks_per_day="How many times per day to check for this query (1-96)",
)
async def set_watchdog_interval(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    checks_per_day: app_commands.Range[int, 1, 96],
):
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    watchdogs = load_watchdogs()
    w = find_watchdog_by_channel(watchdogs, channel.id)
    if not w:
        await interaction.response.send_message("This channel is not a watchdog channel.")
        return

    interval_minutes = max(1, int(24 * 60 / checks_per_day))
    w['interval_minutes'] = interval_minutes
    save_watchdogs(watchdogs)

    await interaction.response.send_message(
        f"Updated check interval for {channel.mention} to roughly every {interval_minutes} minutes "
        f"(~{checks_per_day} times per day)."
    )


@tree.command(name="reset_watchdog", description="Reset a watchdog and redo the initial scan from scratch.")
@app_commands.describe(channel="Watchdog channel to reset")
async def reset_watchdog(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    watchdogs = load_watchdogs()
    w = find_watchdog_by_channel(watchdogs, channel.id)
    if not w:
        await interaction.followup.send("This channel is not a watchdog channel.")
        return

    url = w.get('url')
    if not url:
        await interaction.followup.send("This watchdog has no URL configured; cannot reset.")
        return

    await interaction.followup.send(
        f"Resetting watchdog for `{url}`. Performing a fresh initial scan; this may take several minutes..."
    )

    try:
        channel_name = w.get('name') or channel.name
        watchdog_data_dir = os.path.join('data', channel_name)
        initial_listings = scrape_all_pages(
            url,
            max_pages=999,
            save_artifacts=True,
            output_dir=watchdog_data_dir,
        )
        initial_ids = {listing.get('hash') or listing.get('id') for listing in initial_listings}

        w['last_seen_ids'] = list(initial_ids)
        w['last_check'] = None
        w['created_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
        save_watchdogs(watchdogs)

        try:
            await channel.send(
                f"Watchdog has been reset. New initial scan found {len(initial_ids)} listings. "
                "I will now continue watching for new ones."
            )
        except Exception as e:
            print(f"Failed to send reset info to channel: {e}")

        await interaction.followup.send(
            f"Watchdog for {channel.mention} has been reset and initial scan redone (found {len(initial_ids)} listings)."
        )
    except Exception as e:
        await interaction.followup.send(f"Failed to reset watchdog: {e}")


@tree.command(name="help_watchdog", description="Show help for all Sreality watchdog commands.")
async def help_watchdog(interaction: discord.Interaction):
    """Display a concise overview of all commands and their usage."""
    if interaction.channel_id != COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            "Bot commands can only be used in the designated command channel.",
            ephemeral=True,
        )
        return

    help_text = (
        "**Sreality Watchdog Bot – Commands**\n\n"
        "`/add_watchdog url:<sreality_url> [checks_per_day:<1-96>]`\n"
        "Create a new watchdog for a Sreality search. Performs a deep initial scan, creates a dedicated channel, "
        "and posts new listings there. `checks_per_day` controls how often it's checked (default ~12h).\n\n"
        "`/remove_watchdog channel:<watchdog_channel>`\n"
        "Remove a watchdog and delete its channel.\n\n"
        "`/list_watchdogs`\n"
        "List all active watchdogs with their channels, URLs, intervals, and last check times.\n\n"
        "`/watchdog_info channel:<watchdog_channel>`\n"
        "Show detailed info for a single watchdog (creator, URL, interval, known listings, etc.).\n\n"
        "`/set_watchdog_interval channel:<watchdog_channel> checks_per_day:<1-96>`\n"
        "Change how often a specific watchdog is checked for new listings.\n\n"
        "`/reset_watchdog channel:<watchdog_channel>`\n"
        "Reset a watchdog: redo the initial deep scan from scratch and rebuild the known listings set.\n\n"
        "New listing notifications are posted into each watchdog channel and tag the user who created the watchdog."
    )

    await interaction.response.send_message(help_text)


# --- Background Scraping Task ---
async def check_for_updates():
    await bot.wait_until_ready()
    while not bot.is_closed():
        watchdogs = load_watchdogs()
        now = datetime.datetime.utcnow()
        for w in watchdogs:
            name = w.get('name')
            url = w.get('url')
            interval = w.get('interval_minutes', DEFAULT_INTERVAL_MINUTES)
            last_check_raw = w.get('last_check')
            if last_check_raw:
                try:
                    last_check = datetime.datetime.fromisoformat(last_check_raw.replace('Z', ''))
                except Exception:
                    last_check = None
            else:
                last_check = None

            due = False
            if last_check is None:
                due = True
            else:
                delta_min = (now - last_check).total_seconds() / 60.0
                if delta_min >= interval:
                    due = True

            if not due:
                continue

            print(f"Checking for updates for: {name} (url={url}, interval={interval} min)")
            try:
                all_listings = scrape_all_pages(url, max_pages=5, save_artifacts=False)

                last_seen_ids = set(w.get('last_seen_ids', []))
                new_listings = []

                current_page_ids = set()
                for listing in all_listings:
                    listing_id = listing.get('hash') or listing.get('id')
                    if listing_id:
                        current_page_ids.add(listing_id)
                        if listing_id not in last_seen_ids:
                            new_listings.append(listing)

                if new_listings:
                    print(f"Found {len(new_listings)} new listings for {name}.")
                    creator_id = w.get('created_by')
                    creator_mention = f"<@{creator_id}>" if creator_id else None
                    post_to_webhook(w['webhook_url'], new_listings, creator_mention=creator_mention)

                    # Add the new IDs to the seen list to prevent re-notification
                    updated_ids = last_seen_ids.union(current_page_ids)
                    w['last_seen_ids'] = list(updated_ids)
                else:
                    print(f"No new listings for {name}.")

                w['last_check'] = now.isoformat() + 'Z'
                save_watchdogs(watchdogs)

            except Exception as e:
                print(f"Error checking {name}: {e}")

        # Sleep a short time before next sweep; per-watchdog intervals control actual frequency
        await asyncio.sleep(60)


async def start_health_server():
    async def health(request):
        return web.Response(text="OK", status=200)
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Health server started on port {port}")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    try:
        # Sync commands globally so they show up in the Developer Portal and all guilds where the bot is installed
        synced = await tree.sync()
        print(f"Synced {len(synced)} global command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    bot.loop.create_task(check_for_updates())


async def main():
    await start_health_server()
    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
