import os
import json
import re
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from scraper import scrape_all_pages, cleanup_old_artifacts
import requests

# --- Setup ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID'))
COMMAND_CHANNEL_ID = int(os.getenv('COMMAND_CHANNEL_ID'))
WATCHDOGS_FILE = 'watchdogs.json'

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Watchdog Persistence ---
def load_watchdogs():
    if not os.path.exists(WATCHDOGS_FILE):
        return []
    with open(WATCHDOGS_FILE, 'r') as f:
        return json.load(f)

def save_watchdogs(watchdogs):
    with open(WATCHDOGS_FILE, 'w') as f:
        json.dump(watchdogs, f, indent=2)

# --- Helper Functions ---
def slugify(url):
    """Create a valid Discord channel name from a URL."""
    return re.sub(r'[^a-z0-9-]', '', url.replace('https://www.sreality.cz/hledani/', '').replace('/', '-').lower())[:90]

def post_to_webhook(webhook_url, listings):
    """Posts new listings to a Discord webhook."""
    if not listings:
        return

    messages = []
    for listing in listings:
        name = listing.get('name', 'N/A')
        url = listing.get('listingUrl', '')
        messages.append(f"**New Listing:** {name} - <{url}>")

    # Send messages in chunks of 10 to avoid hitting Discord rate limits
    for i in range(0, len(messages), 10):
        chunk = messages[i:i+10]
        content = "\n".join(chunk)
        try:
            requests.post(webhook_url, json={'content': content})
        except Exception as e:
            print(f"Failed to post to webhook: {e}")


# --- Bot Commands ---
@bot.slash_command(name="add_watchdog", description="Add a new Sreality search to watch.", guild_ids=[GUILD_ID])
async def add_watchdog(ctx: discord.ApplicationContext, url: str):
    """Creates a channel, performs an initial deep scan, and sets up a webhook for a new Sreality URL."""
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.respond("Bot commands can only be used in the designated command channel.", ephemeral=True)
        return

    await ctx.defer()

    watchdogs = load_watchdogs()
    if any(w['url'] == url for w in watchdogs):
        await ctx.respond("This URL is already being watched.")
        return

    channel_name = slugify(url)
    guild = bot.get_guild(GUILD_ID)

    try:
        # Notify user that the initial scan is starting
        await ctx.followup.send(f"Creating watchdog for `{url}`. Performing initial deep scan (up to 250 pages). This may take several minutes...")

        # Create a unique directory for this watchdog's data
        watchdog_data_dir = os.path.join('data', channel_name)

        # Perform the initial deep scan
        initial_listings = scrape_all_pages(url, max_pages=250, save_artifacts=True, output_dir=watchdog_data_dir)
        initial_ids = {listing.get('hash') or listing.get('id') for listing in initial_listings}

        # Create a new channel and webhook
        channel = await guild.create_text_channel(channel_name)
        webhook = await channel.create_webhook(name=f"{channel_name}-updates")

        new_watchdog = {
            "name": channel_name,
            "url": url,
            "channel_id": channel.id,
            "webhook_url": webhook.url,
            "last_seen_ids": list(initial_ids) # Store all found IDs as the baseline
        }
        watchdogs.append(new_watchdog)
        save_watchdogs(watchdogs)

        await ctx.send(f"Watchdog created in channel #{channel_name}. Initial scan complete, found {len(initial_ids)} listings. I will now check for new listings every 12 hours.")
    except Exception as e:
        await ctx.send(f"Failed to create watchdog: {e}")


@bot.slash_command(name="remove_watchdog", description="Remove an existing watchdog.", guild_ids=[GUILD_ID])
async def remove_watchdog(ctx: discord.ApplicationContext, channel: discord.TextChannel):
    """Removes a watchdog and deletes its channel."""
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.respond("Bot commands can only be used in the designated command channel.", ephemeral=True)
        return

    await ctx.defer()

    watchdogs = load_watchdogs()
    watchdog_to_remove = None
    for w in watchdogs:
        if w['channel_id'] == channel.id:
            watchdog_to_remove = w
            break

    if not watchdog_to_remove:
        await ctx.respond("This channel is not a watchdog channel.")
        return

    try:
        # Remove from list and save
        watchdogs.remove(watchdog_to_remove)
        save_watchdogs(watchdogs)

        # Delete the channel
        await channel.delete()

        await ctx.respond(f"Watchdog and channel `{channel.name}` removed successfully.")
    except Exception as e:
        await ctx.respond(f"Failed to remove watchdog: {e}")


# --- Background Scraping Task ---
async def check_for_updates():
    await bot.wait_until_ready()
    while not bot.is_closed():
        watchdogs = load_watchdogs()
        for watchdog in watchdogs:
            print(f"Checking for updates for: {watchdog['name']}")
            try:
                # Scrape the first few pages to get the newest listings
                all_listings = scrape_all_pages(watchdog['url'], max_pages=5, save_artifacts=False)

                last_seen_ids = set(watchdog.get('last_seen_ids', []))
                new_listings = []

                current_page_ids = set()
                for listing in all_listings:
                    listing_id = listing.get('hash') or listing.get('id')
                    if listing_id:
                        current_page_ids.add(listing_id)
                        if listing_id not in last_seen_ids:
                            new_listings.append(listing)

                if new_listings:
                    print(f"Found {len(new_listings)} new listings for {watchdog['name']}.")
                    post_to_webhook(watchdog['webhook_url'], new_listings)

                    # Add the new IDs to the seen list to prevent re-notification
                    watchdog['last_seen_ids'].extend(list(current_page_ids - last_seen_ids))
                    save_watchdogs(watchdogs)

            except Exception as e:
                print(f"Error checking {watchdog['name']}: {e}")

        # Wait for 12 hours before the next check
        await asyncio.sleep(12 * 60 * 60)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    bot.loop.create_task(check_for_updates())

if __name__ == "__main__":
    bot.run(TOKEN)
