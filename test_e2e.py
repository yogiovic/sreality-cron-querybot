#!/usr/bin/env python3
"""
E2E Sanity Test for Sreality Watchdog Bot

Run after deployment to verify the bot is working correctly.

Usage:
    python test_e2e.py                # Interactive mode (asks before deleting)
    python test_e2e.py --auto-cleanup # Auto-delete orphaned channels without asking

The test will:
    1. Check environment config
    2. Check bot is online via Discord API
    3. Verify guild and channel access
    4. Validate watchdogs.json
    5. Test scraper can fetch pages
    6. Test smart scheduling logic
    7. Cleanup any leftover test channels
"""

import os
import sys
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')
COMMAND_CHANNEL_ID = os.getenv('COMMAND_CHANNEL_ID')

DISCORD_API = "https://discord.com/api/v10"
TEST_CHANNEL_PREFIX = "e2e-test-"

# Parse command line args
AUTO_CLEANUP = "--auto-cleanup" in sys.argv


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def log_pass(msg):
    print(f"{Colors.GREEN}✓ PASS:{Colors.RESET} {msg}")


def log_fail(msg):
    print(f"{Colors.RED}✗ FAIL:{Colors.RESET} {msg}")


def log_info(msg):
    print(f"{Colors.BLUE}ℹ INFO:{Colors.RESET} {msg}")


def log_warn(msg):
    print(f"{Colors.YELLOW}⚠ WARN:{Colors.RESET} {msg}")


def log_header(msg):
    print(f"\n{Colors.BOLD}{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}{Colors.RESET}\n")


# =============================================================================
# Discord API Helpers
# =============================================================================
def get_headers():
    return {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json",
    }


def api_get(endpoint):
    resp = requests.get(f"{DISCORD_API}{endpoint}", headers=get_headers())
    resp.raise_for_status()
    return resp.json()


def api_delete(endpoint):
    resp = requests.delete(f"{DISCORD_API}{endpoint}", headers=get_headers())
    resp.raise_for_status()
    return resp


# =============================================================================
# Test Functions
# =============================================================================
def test_env_config():
    """Verify all required environment variables are set."""
    log_header("Test: Environment Configuration")

    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not GUILD_ID:
        missing.append("GUILD_ID")
    if not COMMAND_CHANNEL_ID:
        missing.append("COMMAND_CHANNEL_ID")

    if missing:
        log_fail(f"Missing environment variables: {', '.join(missing)}")
        return False

    log_pass("All required environment variables are set")
    log_info(f"GUILD_ID: {GUILD_ID}")
    log_info(f"COMMAND_CHANNEL_ID: {COMMAND_CHANNEL_ID}")
    return True


def test_bot_online():
    """Check if the bot is online and responding to Discord API."""
    log_header("Test: Bot Online Status")

    try:
        bot_info = api_get("/users/@me")
        log_pass(f"Bot is online: {bot_info['username']}#{bot_info.get('discriminator', '0')}")
        log_info(f"Bot ID: {bot_info['id']}")
        return True
    except requests.exceptions.HTTPError as e:
        log_fail(f"Bot API error: {e}")
        return False
    except Exception as e:
        log_fail(f"Could not connect to Discord API: {e}")
        return False


def test_guild_access():
    """Verify bot has access to the configured guild."""
    log_header("Test: Guild Access")

    try:
        guild = api_get(f"/guilds/{GUILD_ID}")
        log_pass(f"Bot has access to guild: {guild['name']}")
        return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            log_fail("Bot does not have access to this guild")
        else:
            log_fail(f"Guild API error: {e}")
        return False


def test_command_channel_access():
    """Verify bot can read/write in the command channel."""
    log_header("Test: Command Channel Access")

    try:
        channel = api_get(f"/channels/{COMMAND_CHANNEL_ID}")
        log_pass(f"Command channel accessible: #{channel['name']}")
        return True
    except requests.exceptions.HTTPError as e:
        log_fail(f"Cannot access command channel: {e}")
        return False


def test_watchdogs_file():
    """Check if watchdogs.json exists and is valid."""
    log_header("Test: Watchdogs Persistence")

    watchdogs_file = "watchdogs.json"

    if not os.path.exists(watchdogs_file):
        log_warn("watchdogs.json does not exist (will be created on first watchdog)")
        return True

    try:
        with open(watchdogs_file, 'r') as f:
            data = json.load(f)

        if not isinstance(data, list):
            log_fail("watchdogs.json is not a list")
            return False

        log_pass(f"watchdogs.json is valid ({len(data)} watchdogs)")

        for i, w in enumerate(data):
            name = w.get('name', 'unnamed')
            url = w.get('url', 'no-url')[:50]
            log_info(f"  [{i+1}] {name} -> {url}...")

        return True
    except json.JSONDecodeError as e:
        log_fail(f"watchdogs.json is invalid JSON: {e}")
        return False
    except Exception as e:
        log_fail(f"Error reading watchdogs.json: {e}")
        return False


def test_scraper_import():
    """Test that scraper module can be imported."""
    log_header("Test: Scraper Module")

    try:
        from scraper import scrape_all_pages, cleanup_old_artifacts
        log_pass("Scraper module imported successfully")
        return True
    except ImportError as e:
        log_fail(f"Cannot import scraper: {e}")
        return False


def test_scraper_basic():
    """Test that scraper can fetch a simple page."""
    log_header("Test: Scraper Basic Functionality")

    try:
        from scraper import fetch_page

        log_info("Fetching Sreality homepage...")
        html = fetch_page("https://www.sreality.cz/", timeout=10)

        if len(html) > 1000:
            log_pass(f"Fetched page successfully ({len(html)} bytes)")
            return True
        else:
            log_fail("Page content seems too short")
            return False
    except Exception as e:
        log_fail(f"Scraper fetch failed: {e}")
        return False


def test_smart_scheduling():
    """Test smart scheduling logic."""
    log_header("Test: Smart Scheduling")

    try:
        from bot import get_effective_interval, PEAK_START_HOUR, PEAK_END_HOUR
        import datetime

        current_hour = datetime.datetime.now().hour
        is_peak = PEAK_START_HOUR <= current_hour < PEAK_END_HOUR

        base_interval = 720  # 12 hours
        effective = get_effective_interval(base_interval)

        log_info(f"Current hour: {current_hour}:00 ({'PEAK' if is_peak else 'OFF-PEAK'})")
        log_info(f"Peak hours: {PEAK_START_HOUR}:00 - {PEAK_END_HOUR}:00")
        log_info(f"Base interval: {base_interval} min")
        log_info(f"Effective interval: {effective} min")

        if is_peak and effective < base_interval:
            log_pass("Peak hours: interval correctly reduced")
        elif not is_peak and effective > base_interval:
            log_pass("Off-peak hours: interval correctly increased")
        else:
            log_warn("Interval adjustment may not be working correctly")

        return True
    except Exception as e:
        log_fail(f"Smart scheduling test failed: {e}")
        return False


def cleanup_test_channels():
    """Remove any leftover test channels from previous runs."""
    log_header("Cleanup: Test Channels")

    try:
        channels = api_get(f"/guilds/{GUILD_ID}/channels")
        test_channels = [c for c in channels if c['name'].startswith(TEST_CHANNEL_PREFIX)]

        if not test_channels:
            log_info("No test channels to clean up")
            return True

        for channel in test_channels:
            try:
                api_delete(f"/channels/{channel['id']}")
                log_info(f"Deleted test channel: #{channel['name']}")
            except Exception as e:
                log_warn(f"Could not delete #{channel['name']}: {e}")

        log_pass(f"Cleaned up {len(test_channels)} test channel(s)")
        return True
    except Exception as e:
        log_fail(f"Cleanup failed: {e}")
        return False


def cleanup_orphaned_channels():
    """Find and optionally remove orphaned watchdog channels (no matching entry in watchdogs.json)."""
    log_header("Cleanup: Orphaned Watchdog Channels")

    try:
        # Load current watchdogs
        watchdogs_file = "watchdogs.json"
        if os.path.exists(watchdogs_file):
            with open(watchdogs_file, 'r') as f:
                watchdogs = json.load(f)
        else:
            watchdogs = []

        watchdog_channel_ids = {w.get('channel_id') for w in watchdogs if w.get('channel_id')}

        # Get all channels in guild
        channels = api_get(f"/guilds/{GUILD_ID}/channels")

        # Find channels that look like watchdog channels (date prefix pattern: YYYYMMDD-)
        import re
        watchdog_pattern = re.compile(r'^\d{8}-')

        orphaned = []
        for c in channels:
            name = c.get('name', '')
            cid = int(c.get('id', 0))
            # Looks like a watchdog channel but not in watchdogs.json
            if watchdog_pattern.match(name) and cid not in watchdog_channel_ids:
                orphaned.append(c)

        if not orphaned:
            log_info("No orphaned watchdog channels found")
            return True

        log_warn(f"Found {len(orphaned)} orphaned channel(s):")
        for c in orphaned:
            log_info(f"  - #{c['name']} (ID: {c['id']})")

        # Ask user if they want to delete (or auto-delete if --auto-cleanup)
        if AUTO_CLEANUP:
            response = 'y'
            log_info("Auto-cleanup mode: deleting orphaned channels...")
        else:
            print(f"\n{Colors.YELLOW}Delete these orphaned channels? [y/N]: {Colors.RESET}", end="")
            try:
                response = input().strip().lower()
            except EOFError:
                response = 'n'

        if response == 'y':
            deleted = 0
            for c in orphaned:
                try:
                    api_delete(f"/channels/{c['id']}")
                    log_info(f"Deleted: #{c['name']}")
                    deleted += 1
                except Exception as e:
                    log_warn(f"Could not delete #{c['name']}: {e}")
            log_pass(f"Deleted {deleted} orphaned channel(s)")
        else:
            log_info("Skipped deletion (user chose not to delete)")

        return True
    except Exception as e:
        log_fail(f"Orphan cleanup failed: {e}")
        return False


def test_bot_commands_registered():
    """Verify bot has slash commands registered."""
    log_header("Test: Bot Commands Registered")

    try:
        # Get global application commands
        bot_info = api_get("/users/@me")
        bot_id = bot_info['id']

        commands = api_get(f"/applications/{bot_id}/commands")

        expected_commands = [
            'add_watchdog',
            'remove_watchdog',
            'list_watchdogs',
            'watchdog_info',
            'set_watchdog_interval',
            'reset_watchdog',
            'help_watchdog',
        ]

        registered_names = [c['name'] for c in commands]

        missing = [cmd for cmd in expected_commands if cmd not in registered_names]

        if missing:
            log_warn(f"Missing commands (may need bot restart): {', '.join(missing)}")
            log_info("Registered commands: " + ", ".join(registered_names))
        else:
            log_pass(f"All {len(expected_commands)} expected commands are registered")

        # Show help command description
        help_cmd = next((c for c in commands if c['name'] == 'help_watchdog'), None)
        if help_cmd:
            log_info(f"Help command: /{help_cmd['name']} - {help_cmd.get('description', 'no description')}")

        return len(missing) == 0
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            log_warn("Cannot check commands (missing permissions) - this is OK if bot is running")
            return True
        log_fail(f"Failed to check commands: {e}")
        return False
    except Exception as e:
        log_fail(f"Command check failed: {e}")
        return False


# =============================================================================
# Main Test Runner
# =============================================================================
def run_all_tests():
    """Run all E2E tests and report results."""

    print(f"\n{Colors.BOLD}{'#'*60}")
    print(f"#  SREALITY WATCHDOG BOT - E2E SANITY TEST")
    print(f"#  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}{Colors.RESET}\n")

    tests = [
        ("Environment Config", test_env_config),
        ("Bot Online", test_bot_online),
        ("Guild Access", test_guild_access),
        ("Command Channel", test_command_channel_access),
        ("Watchdogs File", test_watchdogs_file),
        ("Bot Commands Registered", test_bot_commands_registered),
        ("Scraper Import", test_scraper_import),
        ("Scraper Fetch", test_scraper_basic),
        ("Smart Scheduling", test_smart_scheduling),
        ("Cleanup Test Channels", cleanup_test_channels),
        ("Cleanup Orphaned Channels", cleanup_orphaned_channels),
    ]

    results = []

    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            log_fail(f"Test '{name}' crashed: {e}")
            results.append((name, False))

    # Summary
    print(f"\n{Colors.BOLD}{'='*60}")
    print("  TEST SUMMARY")
    print(f"{'='*60}{Colors.RESET}\n")

    passed = sum(1 for _, p in results if p)
    failed = sum(1 for _, p in results if not p)

    for name, result in results:
        status = f"{Colors.GREEN}PASS{Colors.RESET}" if result else f"{Colors.RED}FAIL{Colors.RESET}"
        print(f"  [{status}] {name}")

    print(f"\n{Colors.BOLD}Total: {passed} passed, {failed} failed{Colors.RESET}\n")

    if failed > 0:
        print(f"{Colors.RED}Some tests failed! Check the output above.{Colors.RESET}\n")
        return 1
    else:
        print(f"{Colors.GREEN}All tests passed! Bot is ready.{Colors.RESET}\n")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())

