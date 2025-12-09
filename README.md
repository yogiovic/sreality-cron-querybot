# Sreality Watchdog Bot

This project contains a Python-based Discord bot that monitors Sreality.cz search queries and posts new listings to dedicated Discord channels.

## Core Files

- `scraper.py`: A robust, standalone scraper for Sreality that can extract listing data from search result pages.
- `bot.py`: The Discord bot that handles user commands and runs the background scraping tasks.
- `watchdogs.json`: A persistent file storing the list of active search queries (watchdogs).
- `.env`: Configuration file for storing secrets like your Discord token.
- `Dockerfile`: Configuration for building a Docker container to run the bot.
- `requirements.txt`: A list of Python dependencies.

---

## Setup and Deployment on DigitalOcean

Follow these steps to get your bot running on a DigitalOcean Droplet.

### Step 1: Discord Bot Setup

Before deploying, you need to create a Discord application and get the necessary credentials.

1.  **Create the Application:**
    *   Go to the [Discord Developer Portal](https://discord.com/developers/applications).
    *   Click **"New Application"** and give it a name (e.g., "Sreality Watcher").

2.  **Create a Bot User:**
    *   Navigate to the **"Bot"** tab.
    *   Click **"Add Bot"**.
    *   Click **"Reset Token"** to generate and copy your bot's token. **This is your `DISCORD_TOKEN`**.
    *   Enable the **"Message Content Intent"** under the "Privileged Gateway Intents" section.

3.  **Get Server and Channel IDs:**
    *   In your Discord client, enable **Developer Mode** (`User Settings > Advanced`).
    *   **Server ID:** Right-click your server's icon and select **"Copy Server ID"**. **This is your `GUILD_ID`**.
    *   **Command Channel ID:** Create a dedicated channel for bot commands (e.g., `#bot-commands`). Right-click it and select **"Copy Channel ID"**. **This is your `COMMAND_CHANNEL_ID`**.

4.  **Invite the Bot to Your Server:**
    *   In the Developer Portal, go to `OAuth2 > URL Generator`.
    *   Select the scopes: `bot` and `applications.commands`.
    *   Under "Bot Permissions," grant the following:
        *   `Send Messages`
        *   `Manage Channels`
        *   `Manage Webhooks`
    *   Copy the generated URL, paste it into your browser, and authorize the bot to join your server.

5.  **Configure `.env`:**
    *   Open the `.env` file in the project and fill in the credentials you just gathered:
        ```env
        DISCORD_TOKEN=your_bot_token_here
        GUILD_ID=your_server_id_here
        COMMAND_CHANNEL_ID=your_command_channel_id_here
        ```

### Step 2: DigitalOcean Droplet Setup

1.  **Create a Droplet:**
    *   Log in to your [DigitalOcean account](https://cloud.digitalocean.com/).
    *   Create a new **Droplet**.
    *   Choose the **Docker** image from the Marketplace tab.
    *   Select a basic, shared CPU plan (e.g., the cheapest one is sufficient).
    *   Choose a datacenter region.
    *   Set up your authentication (SSH key is recommended).
    *   Click **"Create Droplet"**.

2.  **Connect to Your Droplet:**
    *   Once the Droplet is created, copy its IP address.
    *   Connect to it via SSH:
        ```bash
        ssh root@your_droplet_ip
        ```

### Step 3: Deploy the Bot

1.  **Clone Your Project:**
    *   On the Droplet, clone your project repository. If you're not using Git, you can use a tool like `scp` to upload the files.
        ```bash
        # Example with Git
        git clone your_repository_url
        cd your_project_directory
        ```

2.  **Build the Docker Image:**
    *   Inside your project directory, build the Docker image using the `Dockerfile`.
        ```bash
        docker build -t sreality-bot .
        ```

3.  **Run the Docker Container:**
    *   Run the bot inside a container. The `-d` flag runs it in detached mode (in the background), and `--restart always` ensures it restarts automatically if it crashes or the Droplet reboots.
        ```bash
        docker run -d --name sreality-watchdog --restart always sreality-bot
        ```

### Step 4: Using the Bot

Once the bot is running, you can interact with it in your designated command channel:

*   **Add a new watchdog:**
    ```
    /add_watchdog url:https://www.sreality.cz/hledani/prodej/byty/praha
    ```
    The bot will perform a deep scan, create a new channel (e.g., `#prodej-byty-praha`), and post updates there.

*   **Remove a watchdog:**
    ```
    /remove_watchdog channel:#prodej-byty-praha
    ```
    The bot will delete the channel and stop monitoring the associated URL.

### Managing the Bot on the Server

*   **View logs:** `docker logs -f sreality-watchdog`
*   **Stop the bot:** `docker stop sreality-watchdog`
*   **Start the bot:** `docker start sreality-watchdog`
*   **Remove the container:** `docker rm sreality-watchdog` (after stopping it)

---

## Smart Scheduling

The bot automatically distributes checks more frequently during **peak hours (8:00-22:00)** when most listings are uploaded, and less frequently during off-peak hours (22:00-8:00). 

**No user configuration needed** – just set `checks_per_day` when adding a watchdog (or use the default), and the bot handles the rest:

- During **peak hours**: checks happen ~2x more often
- During **off-peak hours**: checks happen ~2x less often

For example, if you set 4 checks per day (~360 min base interval):
- Peak hours: effective interval ~216 min
- Off-peak hours: effective interval ~648 min

This ensures you get notified faster when new listings are most likely to appear.

