#!/usr/bin/env python3
"""
Paracord - Discord Message Bulk Deletion Tool

Safely bulk delete your Discord messages across servers, DMs, and group DMs.
Uses cursor-based pagination to bypass Discord's 10K message offset ceiling.

Inspired by undiscord's approach, built from scratch to handle 100K+ messages
unattended with progress persistence, ghost message optimization, and
multi-target config-driven operation.

Usage:
    python3 paracord.py --discover                    # Find your servers/channels
    python3 paracord.py --config config.json --dry-run  # Preview
    python3 paracord.py --config config.json            # Execute
    python3 paracord.py --config config.json --resume   # Resume after interrupt
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("Error: 'requests' library not found.")
    print("Install it with: pip3 install requests")
    sys.exit(1)

__version__ = "3.2.0"

# Console colors (ANSI escape codes, works on most terminals)
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Constants
DISCORD_API_BASE = "https://discord.com/api/v9"
PROGRESS_FILE = ".paracord_progress.json"
LOG_FILE = "paracord.log"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MEOW_TEXT = "Meow Meow Meow Meow"
MEOW_MODES = ("off", "edit_and_delete", "edit_only")

class ProgressBar:
    """Simple progress bar for terminal display"""
    
    def __init__(self, total: int, prefix: str = '', length: int = 50):
        self.total = total
        self.prefix = prefix
        self.length = length
        self.current = 0
        
    def update(self, current: int):
        self.current = current
        percent = 100 * (self.current / float(self.total))
        filled = int(self.length * self.current // self.total)
        bar = '█' * filled + '░' * (self.length - filled)
        
        print(f'\r{self.prefix} |{bar}| {percent:.1f}% ({self.current}/{self.total})', end='')
        
        if self.current == self.total:
            print()
    
    def finish(self):
        self.update(self.total)


class Paracord:
    """Main class for Discord message deletion"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.token = None
        self.author_id = None
        self.session = requests.Session()
        
        # Statistics
        self.stats = {
            'deleted': 0,
            'edited': 0,
            'failed': 0,
            'skipped': 0,
            'rate_limited': 0,
            'ghosts': 0,
            'start_time': None,
            'end_time': None
        }
        
        # Progress tracking
        self.progress_data = {}
        self.current_target_index = 0
        self.should_stop = False
        
        # Set up logging
        self.setup_logging()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def setup_logging(self):
        """Configure logging to file and console"""
        log_format = '%(asctime)s [%(levelname)s] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        
        # File handler
        file_handler = logging.FileHandler(LOG_FILE, mode='a')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        
        # Console handler (less verbose)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        
        # Configure root logger
        logging.basicConfig(
            level=logging.DEBUG,
            handlers=[file_handler, console_handler]
        )
        
        self.logger = logging.getLogger(__name__)
    
    def signal_handler(self, signum, frame):
        """Handle graceful shutdown on Ctrl+C"""
        print(f"\n\n{Colors.YELLOW}Received interrupt signal. Saving progress...{Colors.ENDC}")
        self.should_stop = True
        self.save_progress()
        print(f"{Colors.GREEN}Progress saved. You can resume with --resume flag.{Colors.ENDC}")
        sys.exit(0)
    
    def load_token(self, token_arg: Optional[str] = None) -> str:
        """Load Discord token from various sources.
        
        Priority:
            1. --token command-line argument
            2. DISCORD_TOKEN environment variable
            3. .env file in current directory
        """
        
        # Priority 1: Command-line argument
        if token_arg:
            self.logger.info("Using token from command-line argument")
            return token_arg
        
        # Priority 2: Environment variable
        if 'DISCORD_TOKEN' in os.environ:
            print(f"{Colors.GREEN}Using token from DISCORD_TOKEN environment variable{Colors.ENDC}")
            self.logger.info("Using token from environment variable")
            return os.environ['DISCORD_TOKEN']
        
        # Priority 3: .env file
        env_file = Path('.env')
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DISCORD_TOKEN='):
                        token = line.split('=', 1)[1].strip().strip('"\'')
                        print(f"{Colors.GREEN}Using token from .env file{Colors.ENDC}")
                        self.logger.info("Using token from .env file")
                        return token
        
        # No token found
        print(f"{Colors.RED}Error: No Discord token found!{Colors.ENDC}")
        print("\nProvide your token using one of these methods:")
        print("  1. Create a .env file:  echo 'DISCORD_TOKEN=your_token' > .env")
        print("  2. Set env variable:    export DISCORD_TOKEN='your_token'")
        print("  3. Use --token flag:    python3 paracord.py --token 'your_token'")
        print("\nSee README.md for instructions on obtaining your Discord token.")
        sys.exit(1)
    
    def validate_token(self) -> Tuple[bool, Optional[str]]:
        """Validate token and get user ID"""
        print(f"{Colors.CYAN}Validating Discord token...{Colors.ENDC}")
        
        self.session.headers.update({
            'Authorization': self.token,
            'User-Agent': USER_AGENT
        })
        
        try:
            response = self.session.get(f"{DISCORD_API_BASE}/users/@me", timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                user_id = user_data['id']
                username = user_data['username']
                discriminator = user_data.get('discriminator', '0')
                
                print(f"{Colors.GREEN}  Token valid{Colors.ENDC}")
                if discriminator != '0':
                    print(f"  Logged in as: {username}#{discriminator} (ID: {user_id})")
                else:
                    print(f"  Logged in as: @{username} (ID: {user_id})")
                
                self.logger.info(f"Token validated for user: {username} ({user_id})")
                return True, user_id
            
            elif response.status_code == 401:
                print(f"{Colors.RED}  Invalid token (401 Unauthorized){Colors.ENDC}")
                self.logger.error("Token validation failed: Invalid credentials")
                return False, None
            
            else:
                print(f"{Colors.RED}  Validation failed (HTTP {response.status_code}){Colors.ENDC}")
                self.logger.error(f"Token validation failed: {response.status_code}")
                return False, None
        
        except requests.exceptions.RequestException as e:
            print(f"{Colors.RED}  Network error: {e}{Colors.ENDC}")
            self.logger.error(f"Token validation network error: {e}")
            return False, None
    
    def discover_servers(self):
        """Discover user's servers and channels, generate config.json"""
        print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"{Colors.HEADER}DISCORD SERVER & CHANNEL DISCOVERY{Colors.ENDC}")
        print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
        
        print(f"{Colors.CYAN}Fetching your servers...{Colors.ENDC}")
        
        try:
            # Get user's guilds
            response = self.session.get(f"{DISCORD_API_BASE}/users/@me/guilds", timeout=10)
            response.raise_for_status()
            guilds = response.json()
            
            # Get DM channels
            dm_response = self.session.get(f"{DISCORD_API_BASE}/users/@me/channels", timeout=10)
            dm_response.raise_for_status()
            dms = dm_response.json()
            
            print(f"\n{Colors.GREEN}Found {len(guilds)} servers and {len(dms)} DM channels{Colors.ENDC}\n")
            
            # Display servers
            print(f"{Colors.BOLD}YOUR SERVERS:{Colors.ENDC}")
            for i, guild in enumerate(guilds, 1):
                print(f"  {i}. {guild['name']} (ID: {guild['id']})")
            
            # Display DMs
            print(f"\n{Colors.BOLD}YOUR DMs:{Colors.ENDC}")
            for i, dm in enumerate(dms, 1):
                if dm['type'] == 1:  # DM
                    recipient = dm['recipients'][0]
                    username = recipient.get('username', 'Unknown')
                    print(f"  {i}. @{username} (ID: {dm['id']})")
                elif dm['type'] == 3:  # Group DM
                    name = dm.get('name', 'Unnamed Group')
                    print(f"  {i}. Group: {name} (ID: {dm['id']})")
            
            # Ask if user wants to create config
            print(f"\n{Colors.YELLOW}Create config.json for batch processing?{Colors.ENDC}")
            choice = input("Enter 'y' to select servers, 'n' to exit: ").lower().strip()
            
            if choice == 'y':
                self.create_config_interactive(guilds, dms)
            else:
                print(f"\n{Colors.CYAN}You can manually create config.json or re-run with --discover.{Colors.ENDC}")
        
        except requests.exceptions.RequestException as e:
            print(f"{Colors.RED}Error fetching servers: {e}{Colors.ENDC}")
            self.logger.error(f"Server discovery failed: {e}")
            sys.exit(1)
    
    def create_config_interactive(self, guilds: List[Dict], dms: List[Dict]):
        """Interactive config file creation"""
        print(f"\n{Colors.CYAN}Select servers to process (comma-separated numbers, or 'all'):{Colors.ENDC}")
        selection = input("Enter selection: ").strip()
        
        targets = []
        
        if selection.lower() == 'all':
            selected_guilds = guilds
        else:
            try:
                indices = [int(x.strip()) - 1 for x in selection.split(',')]
                selected_guilds = [guilds[i] for i in indices if 0 <= i < len(guilds)]
            except (ValueError, IndexError):
                print(f"{Colors.RED}Invalid selection{Colors.ENDC}")
                return
        
        # For each selected guild, get channels
        for guild in selected_guilds:
            print(f"\n{Colors.CYAN}Fetching channels for: {guild['name']}{Colors.ENDC}")
            
            try:
                response = self.session.get(
                    f"{DISCORD_API_BASE}/guilds/{guild['id']}/channels",
                    timeout=10
                )
                response.raise_for_status()
                channels = response.json()
                
                # Filter text channels (Text, Announcement, Forum)
                text_channels = [c for c in channels if c['type'] in [0, 5, 15]]
                
                print(f"  Found {len(text_channels)} text channels")
                print(f"  Add all channels from this server? (y/n): ", end='')
                
                if input().lower().strip() == 'y':
                    for channel in text_channels:
                        targets.append({
                            "type": "guild",
                            "guild_id": guild['id'],
                            "guild_name": guild['name'],
                            "channel_id": channel['id'],
                            "channel_name": channel['name']
                        })
            
            except requests.exceptions.RequestException as e:
                print(f"{Colors.RED}  Error fetching channels: {e}{Colors.ENDC}")
        
        # Add DMs if requested
        print(f"\n{Colors.CYAN}Include DMs? (y/n):{Colors.ENDC}", end=' ')
        if input().lower().strip() == 'y':
            for dm in dms:
                if dm['type'] == 1:
                    recipient = dm['recipients'][0]
                    targets.append({
                        "type": "dm",
                        "channel_id": dm['id'],
                        "recipient_name": recipient.get('username', 'Unknown')
                    })
                elif dm['type'] == 3:
                    targets.append({
                        "type": "group_dm",
                        "channel_id": dm['id'],
                        "group_name": dm.get('name', 'Unnamed Group')
                    })
        
        # Create config
        config = {
            "auth_token_env": "DISCORD_TOKEN",
            "author_id": self.author_id,
            "settings": {
                "search_delay": 10,
                "delete_delay": 1,
                "skip_pinned": True,
                "skip_meowed": False,
                "max_retries": 3,
                "dry_run": False
            },
            "targets": targets
        }
        
        # Save config
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"\n{Colors.GREEN}Configuration saved to config.json{Colors.ENDC}")
        print(f"{Colors.GREEN}  Total targets: {len(targets)}{Colors.ENDC}")
        print(f"\n{Colors.CYAN}Next steps:{Colors.ENDC}")
        print(f"  1. Review config.json and adjust settings if needed")
        print(f"  2. Test with: python3 paracord.py --config config.json --dry-run")
        print(f"  3. Execute with: python3 paracord.py --config config.json")
    
    def search_messages(self, guild_id: str, channel_id: str, offset: int = 0,
                        max_id: Optional[str] = None) -> Dict:
        """Search for messages using cursor-based pagination.
        
        Uses max_id as a sliding cursor to walk backward through time,
        bypassing Discord's 9,975 offset ceiling. The offset param is only
        used to skip undeletable messages within a single search page.
        
        Args:
            guild_id: The guild ID, or "@me" for DMs.
            channel_id: The channel ID.
            offset: Offset within current result page.
            max_id: Snowflake cursor - only return messages older than this ID.
        """
        
        if guild_id == "@me":
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/search"
        else:
            url = f"{DISCORD_API_BASE}/guilds/{guild_id}/messages/search"
        
        params = {
            'author_id': self.author_id,
            'include_nsfw': 'true',
            'sort_by': 'timestamp',
            'sort_order': 'desc',
            'offset': offset
        }
        
        if guild_id != "@me":
            params['channel_id'] = channel_id
        
        # Cursor-based pagination: only fetch messages older than max_id
        if max_id is not None:
            params['max_id'] = max_id
        
        self.logger.debug(f"Searching messages: {url}?{urlencode(params)}")
        
        response = self.session.get(url, params=params, timeout=30)
        
        # Handle rate limiting with dynamic backoff
        if response.status_code == 429:
            retry_after = response.json().get('retry_after', 40)
            wait_time = retry_after * 2
            self.stats['rate_limited'] += 1
            self.logger.warning(f"Rate limited on search, waiting {wait_time}s (retry_after={retry_after}s)")
            print(f"{Colors.YELLOW}Rate limited - waiting {wait_time}s...{Colors.ENDC}")
            time.sleep(wait_time)
            return self.search_messages(guild_id, channel_id, offset, max_id)
        
        # Handle channel not indexed
        if response.status_code == 202:
            retry_after = response.json().get('retry_after', 5)
            self.logger.info(f"Channel not indexed, waiting {retry_after}s")
            print(f"{Colors.YELLOW}Channel being indexed, waiting {retry_after}s...{Colors.ENDC}")
            time.sleep(retry_after)
            return self.search_messages(guild_id, channel_id, offset, max_id)
        
        response.raise_for_status()
        return response.json()
    
    def delete_message(self, channel_id: str, message_id: str, attempt: int = 1) -> str:
        """Delete a single message.
        
        Returns:
            'OK'    - Successfully deleted
            'GHOST' - Already deleted (stale search index entry)
            'SKIP'  - Cannot delete (archived thread, no permissions)
            'RETRY' - Rate limited, should retry
            'FAILED'- Permanent failure
        """
        
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"
        
        try:
            response = self.session.delete(url, timeout=10)
            
            if response.status_code == 204:
                return 'OK'
            
            elif response.status_code == 429:
                # Rate limited - dynamic backoff (double the retry_after)
                retry_after = response.json().get('retry_after', 3)
                wait_time = retry_after * 2
                self.stats['rate_limited'] += 1
                self.logger.warning(f"Rate limited on delete, waiting {wait_time}s (retry_after={retry_after}s)")
                print(f"{Colors.YELLOW}Rate limited - waiting {wait_time}s...{Colors.ENDC}")
                time.sleep(wait_time)
                return 'RETRY'
            
            elif response.status_code == 404:
                # Already deleted (stale search index ghost) - no delay needed
                self.logger.debug(f"Message {message_id} already deleted (ghost)")
                return 'GHOST'
            
            elif response.status_code == 400:
                # Check for archived thread (code 50083)
                try:
                    error_data = response.json()
                    if error_data.get('code') == 50083:
                        self.logger.warning(f"Message {message_id} in archived thread, skipping")
                        return 'SKIP'
                except (ValueError, KeyError):
                    pass
                self.logger.error(f"Delete failed with status 400: {response.text}")
                return 'FAILED'
            
            elif response.status_code == 403:
                # Forbidden (no permissions)
                error_data = response.json()
                self.logger.warning(f"Cannot delete message {message_id}: {error_data}")
                return 'SKIP'
            
            else:
                self.logger.error(f"Delete failed with status {response.status_code}: {response.text}")
                return 'FAILED'
        
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Delete request failed: {e}")
            return 'RETRY' if attempt < 3 else 'FAILED'
    
    def edit_message(self, channel_id: str, message_id: str, content: str,
                     attempt: int = 1) -> str:
        """Edit a single message's content.
        
        Returns:
            'OK'     - Successfully edited
            'GHOST'  - Message doesn't exist (404)
            'SKIP'   - Cannot edit (archived thread, no permissions)
            'RETRY'  - Rate limited, should retry
            'FAILED' - Permanent failure
        """
        
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"
        payload = {'content': content}
        
        try:
            response = self.session.patch(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                return 'OK'
            
            elif response.status_code == 429:
                retry_after = response.json().get('retry_after', 3)
                wait_time = retry_after * 2
                self.stats['rate_limited'] += 1
                self.logger.warning(f"Rate limited on edit, waiting {wait_time}s (retry_after={retry_after}s)")
                print(f"{Colors.YELLOW}Rate limited - waiting {wait_time}s...{Colors.ENDC}")
                time.sleep(wait_time)
                return 'RETRY'
            
            elif response.status_code == 404:
                self.logger.debug(f"Message {message_id} not found for edit (ghost)")
                return 'GHOST'
            
            elif response.status_code == 400:
                try:
                    error_data = response.json()
                    if error_data.get('code') == 50083:
                        self.logger.warning(f"Message {message_id} in archived thread, cannot edit")
                        return 'SKIP'
                except (ValueError, KeyError):
                    pass
                self.logger.error(f"Edit failed with status 400: {response.text}")
                return 'FAILED'
            
            elif response.status_code == 403:
                error_data = response.json()
                self.logger.warning(f"Cannot edit message {message_id}: {error_data}")
                return 'SKIP'
            
            else:
                self.logger.error(f"Edit failed with status {response.status_code}: {response.text}")
                return 'FAILED'
        
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Edit request failed: {e}")
            return 'RETRY' if attempt < 3 else 'FAILED'
    
    def process_target(self, target: Dict, dry_run: bool = False):
        """Process a single channel/DM target using cursor-based pagination.
        
        Uses max_id as a sliding cursor to walk backward through time,
        bypassing Discord's 9,975 offset ceiling. The offset is only used
        to skip undeletable messages within a single search page.
        """
        
        # Build target name based on type
        if target['type'] == 'guild':
            target_name = f"#{target['channel_name']} ({target['guild_name']})"
        elif target['type'] == 'dm':
            target_name = f"DM: @{target['recipient_name']}"
        elif target['type'] == 'group_dm':
            target_name = f"Group: {target['group_name']}"
        else:
            target_name = "Unknown"
        
        print(f"\n{Colors.HEADER}{'─'*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}Target: {target_name}{Colors.ENDC}")
        print(f"{Colors.HEADER}{'─'*60}{Colors.ENDC}")
        
        # For DMs and Group DMs, use "@me" as guild_id
        if target['type'] in ['dm', 'group_dm']:
            guild_id = "@me"
        else:
            guild_id = target['guild_id']
        
        channel_id = target['channel_id']
        
        # Cursor-based pagination state
        max_id_cursor = None  # None = start from newest message
        offset = 0            # Only used to skip undeletable messages within a page
        messages_found = 0
        empty_pages = 0       # Track consecutive empty pages to detect end
        MAX_EMPTY_PAGES = 3   # Stop after this many consecutive empty pages
        
        while not self.should_stop:
            # Search for messages
            cursor_info = f"max_id={max_id_cursor}" if max_id_cursor else "from newest"
            print(f"{Colors.CYAN}Searching messages ({cursor_info}, offset={offset})...{Colors.ENDC}")
            
            try:
                result = self.search_messages(guild_id, channel_id, offset=offset,
                                              max_id=max_id_cursor)
            except requests.exceptions.RequestException as e:
                print(f"{Colors.RED}Error searching: {e}{Colors.ENDC}")
                break
            
            # Extract messages
            total_results = result.get('total_results', 0)
            message_groups = result.get('messages', [])
            
            if not message_groups:
                empty_pages += 1
                if empty_pages >= MAX_EMPTY_PAGES:
                    print(f"{Colors.GREEN}No more messages found (after {empty_pages} empty pages){Colors.ENDC}")
                    break
                # Sometimes the index needs a moment; wait and retry
                print(f"{Colors.YELLOW}Empty page ({empty_pages}/{MAX_EMPTY_PAGES}), waiting before retry...{Colors.ENDC}")
                time.sleep(self.config['settings']['search_delay'])
                continue
            
            # Reset empty page counter since we got results
            empty_pages = 0
            
            # Flatten message groups and extract all hit messages
            all_hit_messages = []
            messages = []
            for group in message_groups:
                for msg in group:
                    if msg.get('hit') and msg.get('author', {}).get('id') == self.author_id:
                        all_hit_messages.append(msg)
                        # Filter pinned if configured
                        if self.config['settings']['skip_pinned'] and msg.get('pinned'):
                            continue
                        # Skip meowed messages if configured
                        if self.config['settings'].get('skip_meowed') and msg.get('content') == MEOW_TEXT:
                            continue
                        messages.append(msg)
            
            if not messages and not all_hit_messages:
                # No messages from us in this page - advance cursor
                oldest_id = None
                for group in message_groups:
                    for msg in group:
                        if msg.get('hit'):
                            msg_id = int(msg['id'])
                            if oldest_id is None or msg_id < oldest_id:
                                oldest_id = msg_id
                if oldest_id:
                    max_id_cursor = str(oldest_id - 1)
                    offset = 0
                    print(f"{Colors.YELLOW}No messages from us in batch, advancing cursor...{Colors.ENDC}")
                else:
                    offset += len(message_groups)
                    print(f"{Colors.YELLOW}No deletable messages in this batch, advancing offset...{Colors.ENDC}")
                time.sleep(self.config['settings']['search_delay'])
                continue
            
            if not messages and all_hit_messages:
                # All our messages were pinned/filtered - advance cursor past them
                oldest_id = min(int(msg['id']) for msg in all_hit_messages)
                max_id_cursor = str(oldest_id - 1)
                offset = 0
                print(f"{Colors.YELLOW}All messages in batch were filtered (pinned/meowed), advancing cursor...{Colors.ENDC}")
                time.sleep(self.config['settings']['search_delay'])
                continue
            
            messages_found += len(messages)
            print(f"{Colors.GREEN}Found {len(messages)} messages to process{Colors.ENDC}")
            print(f"{Colors.CYAN}Total found so far: {messages_found} / ~{total_results}{Colors.ENDC}")
            
            if dry_run:
                # Just preview
                print(f"\n{Colors.YELLOW}[DRY RUN] Would delete:{Colors.ENDC}")
                for msg in messages[:5]:
                    content = msg.get('content', '[no content]')[:50]
                    timestamp = msg.get('timestamp', 'unknown')[:10]
                    print(f"  - {timestamp}: {content}")
                if len(messages) > 5:
                    print(f"  ... and {len(messages) - 5} more")
                # Advance cursor past this batch
                oldest_id = min(int(msg['id']) for msg in messages)
                max_id_cursor = str(oldest_id - 1)
                offset = 0
            else:
                # Actually process messages (edit and/or delete)
                meow_mode = self.config['settings'].get('meow_mode', 'off')
                if meow_mode != 'off':
                    prefix_label = 'Meowing' if meow_mode == 'edit_only' else 'Meowing & deleting'
                else:
                    prefix_label = 'Deleting'
                progress = ProgressBar(len(messages), prefix=prefix_label)
                
                deleted_in_batch = 0
                edited_in_batch = 0
                skipped_in_batch = 0
                oldest_processed_id = None
                
                for i, msg in enumerate(messages):
                    if self.should_stop:
                        break
                    
                    message_id = msg['id']
                    max_retries = self.config['settings']['max_retries']
                    
                    # Track the oldest message for cursor advancement
                    msg_id_int = int(message_id)
                    if oldest_processed_id is None or msg_id_int < oldest_processed_id:
                        oldest_processed_id = msg_id_int
                    
                    # Meow mode: edit message content before deletion
                    was_ghost = False
                    if meow_mode != 'off':
                        # Skip edit if message is already meowed
                        if msg.get('content') != MEOW_TEXT:
                            edit_success = False
                            for attempt in range(1, max_retries + 1):
                                edit_result = self.edit_message(channel_id, message_id, MEOW_TEXT, attempt)
                                
                                if edit_result == 'OK':
                                    self.stats['edited'] += 1
                                    edited_in_batch += 1
                                    edit_success = True
                                    break
                                elif edit_result == 'GHOST':
                                    self.stats['ghosts'] += 1
                                    deleted_in_batch += 1
                                    was_ghost = True
                                    break
                                elif edit_result in ('SKIP', 'FAILED'):
                                    break
                                elif edit_result == 'RETRY':
                                    if attempt < max_retries:
                                        time.sleep(1)
                                        continue
                                    break
                            
                            # If ghost, skip deletion (message doesn't exist)
                            if was_ghost:
                                progress.update(i + 1)
                                continue
                            
                            # Delay after edit before next API call
                            if edit_success:
                                time.sleep(self.config['settings']['delete_delay'])
                    
                    # In edit_only mode, skip deletion entirely
                    if meow_mode == 'edit_only':
                        progress.update(i + 1)
                        # Still need a delay between edits
                        if not was_ghost:
                            time.sleep(self.config['settings']['delete_delay'])
                        continue
                    
                    # Attempt deletion with retries
                    for attempt in range(1, max_retries + 1):
                        result = self.delete_message(channel_id, message_id, attempt)
                        
                        if result == 'OK':
                            self.stats['deleted'] += 1
                            deleted_in_batch += 1
                            break
                        elif result == 'GHOST':
                            self.stats['ghosts'] += 1
                            deleted_in_batch += 1
                            was_ghost = True
                            break
                        elif result == 'SKIP':
                            self.stats['skipped'] += 1
                            skipped_in_batch += 1
                            break
                        elif result == 'FAILED':
                            self.stats['failed'] += 1
                            skipped_in_batch += 1
                            break
                        elif result == 'RETRY':
                            if attempt < max_retries:
                                time.sleep(1)
                                continue
                            else:
                                self.stats['failed'] += 1
                                skipped_in_batch += 1
                                break
                    
                    progress.update(i + 1)
                    
                    # Skip delay for ghost messages (already deleted, no API cost)
                    if not was_ghost:
                        time.sleep(self.config['settings']['delete_delay'])
                
                progress.finish()
                
                # Advance the cursor past everything we processed.
                # This is the key: instead of incrementing offset (which hits
                # Discord's 9,975 ceiling), we slide max_id backward through time.
                if oldest_processed_id is not None:
                    max_id_cursor = str(oldest_processed_id - 1)
                    offset = 0  # Reset offset since cursor handles pagination
                    self.logger.info(
                        f"Cursor advanced: max_id={max_id_cursor} "
                        f"(edited={edited_in_batch}, deleted={deleted_in_batch}, skipped={skipped_in_batch})"
                    )
                
                # Build batch summary
                parts = []
                if edited_in_batch:
                    parts.append(f"{edited_in_batch} meowed")
                if deleted_in_batch:
                    parts.append(f"{deleted_in_batch} deleted")
                if skipped_in_batch:
                    parts.append(f"{skipped_in_batch} skipped")
                print(f"{Colors.CYAN}Batch done: {', '.join(parts)}{Colors.ENDC}")
            
            # Delay before next search
            print(f"{Colors.CYAN}Waiting {self.config['settings']['search_delay']}s before next search...{Colors.ENDC}")
            time.sleep(self.config['settings']['search_delay'])
    
    def run_batch(self, config_file: str, dry_run: bool = False, resume: bool = False,
                  skip_confirm: bool = False, meow_mode: Optional[str] = None,
                  skip_meowed: Optional[bool] = None):
        """Run batch deletion from config file"""
        
        print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"{Colors.HEADER}PARACORD v{__version__}{Colors.ENDC}")
        print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
        
        # Load config
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        self.config = config
        
        # CLI --meow flag overrides config file meow_mode
        if meow_mode is not None:
            config['settings']['meow_mode'] = meow_mode
        # Ensure meow_mode has a default
        config['settings'].setdefault('meow_mode', 'off')
        
        # CLI --skip-meowed flag overrides config file skip_meowed
        if skip_meowed is not None:
            config['settings']['skip_meowed'] = skip_meowed
        # Ensure skip_meowed has a default
        config['settings'].setdefault('skip_meowed', False)
        
        # Load token
        self.token = self.load_token()
        
        # Validate token
        valid, user_id = self.validate_token()
        if not valid:
            sys.exit(1)
        
        self.author_id = user_id
        
        # Load progress if resuming
        if resume and Path(PROGRESS_FILE).exists():
            with open(PROGRESS_FILE, 'r') as f:
                self.progress_data = json.load(f)
            print(f"{Colors.GREEN}Loaded saved progress{Colors.ENDC}")
            self.current_target_index = self.progress_data.get('current_target_index', 0)
        
        # Get targets
        targets = [t for t in config['targets'] if t.get('enabled', True)]
        
        if not targets:
            print(f"{Colors.RED}No enabled targets found in config{Colors.ENDC}")
            sys.exit(1)
        
        print(f"{Colors.BOLD}Configuration loaded:{Colors.ENDC}")
        print(f"  Targets: {len(targets)}")
        print(f"  Search delay: {config['settings']['search_delay']}s")
        print(f"  Delete delay: {config['settings']['delete_delay']}s")
        print(f"  Skip pinned: {config['settings']['skip_pinned']}")
        if config['settings'].get('skip_meowed'):
            print(f"  Skip meowed: {Colors.YELLOW}True{Colors.ENDC} (messages containing \"{MEOW_TEXT}\" will be preserved)")
        
        meow_mode = config['settings'].get('meow_mode', 'off')
        if meow_mode != 'off':
            print(f"  Meow mode: {Colors.YELLOW}{meow_mode}{Colors.ENDC}")
        
        if dry_run:
            print(f"{Colors.YELLOW}  DRY RUN MODE: No messages will be deleted{Colors.ENDC}")
        
        # Confirm
        if not dry_run and not skip_confirm:
            if meow_mode == 'edit_only':
                action_desc = f"edit messages to \"{MEOW_TEXT}\" in"
            elif meow_mode == 'edit_and_delete':
                action_desc = f"edit messages to \"{MEOW_TEXT}\" then delete them from"
            else:
                action_desc = "delete messages from"
            print(f"\n{Colors.YELLOW}This will {action_desc} {len(targets)} channels/DMs.{Colors.ENDC}")
            print(f"{Colors.YELLOW}This action cannot be undone!{Colors.ENDC}")
            confirm = input("Continue? (yes/no): ").lower().strip()
            if confirm != 'yes':
                print(f"{Colors.CYAN}Aborted.{Colors.ENDC}")
                sys.exit(0)
        
        # Start timer
        self.stats['start_time'] = datetime.now()
        
        # Process targets
        for i in range(self.current_target_index, len(targets)):
            if self.should_stop:
                break
            
            target = targets[i]
            print(f"\n{Colors.BOLD}[{i+1}/{len(targets)}] Processing target...{Colors.ENDC}")
            
            self.process_target(target, dry_run)
            self.current_target_index = i + 1
            self.save_progress()
        
        # End timer
        self.stats['end_time'] = datetime.now()
        
        # Print summary
        self.print_summary()
    
    def save_progress(self):
        """Save current progress to file"""
        progress = {
            'current_target_index': self.current_target_index,
            'stats': self.stats,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(progress, f, indent=2, default=str)
        
        self.logger.info(f"Progress saved: target {self.current_target_index}")
    
    def print_summary(self):
        """Print execution summary"""
        print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"{Colors.HEADER}SUMMARY{Colors.ENDC}")
        print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
        
        duration = self.stats['end_time'] - self.stats['start_time']
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        seconds = int(duration.total_seconds() % 60)
        
        print(f"{Colors.BOLD}Duration:{Colors.ENDC} {hours}h {minutes}m {seconds}s")
        if self.stats['edited']:
            print(f"{Colors.YELLOW}Edited (meowed):{Colors.ENDC} {self.stats['edited']}")
        print(f"{Colors.GREEN}Deleted:{Colors.ENDC} {self.stats['deleted']}")
        print(f"{Colors.YELLOW}Ghosts:{Colors.ENDC} {self.stats['ghosts']} (already-deleted stale index entries)")
        print(f"{Colors.YELLOW}Skipped:{Colors.ENDC} {self.stats['skipped']}")
        print(f"{Colors.RED}Failed:{Colors.ENDC} {self.stats['failed']}")
        print(f"{Colors.CYAN}Rate limited:{Colors.ENDC} {self.stats['rate_limited']} times")
        
        print(f"\n{Colors.CYAN}Full log saved to: {LOG_FILE}{Colors.ENDC}")


def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(
        description='Paracord - Bulk delete your Discord messages safely',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover servers and create config
  python3 paracord.py --discover
  
  # Test with dry-run
  python3 paracord.py --config config.json --dry-run
  
  # Execute deletion
  python3 paracord.py --config config.json
  
  # Resume from interruption
  python3 paracord.py --config config.json --resume
  
  # Skip confirmation prompt
  python3 paracord.py --config config.json --yes
  
  # Meow mode: edit all messages to "Meow Meow Meow Meow" then delete
  python3 paracord.py --config config.json --meow
  
   # Meow mode: edit only (leave messages standing as meows)
   python3 paracord.py --config config.json --meow edit_only
   
   # Delete all messages except meowed ones (preserve meow'd messages)
   python3 paracord.py --config config.json --skip-meowed
        """
    )
    
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--token', '-t', help='Discord auth token (optional, can also use .env or env var)')
    parser.add_argument('--config', '-c', help='Path to config.json file')
    parser.add_argument('--discover', '-d', action='store_true', help='Discover servers and channels')
    parser.add_argument('--dry-run', action='store_true', help='Preview without deleting')
    parser.add_argument('--resume', '-r', action='store_true', help='Resume from saved progress')
    parser.add_argument('--verify-auth', action='store_true', help='Verify token and exit')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--meow', nargs='?', const='edit_and_delete', default=None,
                        choices=['edit_and_delete', 'edit_only'],
                        help='Meow mode: edit messages to "Meow Meow Meow Meow" before deleting. '
                             'Use "edit_only" to leave meowed messages standing (default: edit_and_delete)')
    parser.add_argument('--skip-meowed', action='store_true', default=None,
                        help='Skip messages already containing "Meow Meow Meow Meow" (preserve meowed messages)')
    
    args = parser.parse_args()
    
    # Create initial config with defaults
    config = {
        "settings": {
            "search_delay": 10,
            "delete_delay": 1,
            "skip_pinned": True,
            "skip_meowed": False,
            "max_retries": 3
        }
    }
    
    paracord = Paracord(config)
    
    # Load token
    paracord.token = paracord.load_token(args.token)
    
    # Verify auth
    valid, user_id = paracord.validate_token()
    if not valid:
        sys.exit(1)
    
    paracord.author_id = user_id
    
    if args.verify_auth:
        print(f"\n{Colors.GREEN}Token is valid!{Colors.ENDC}")
        sys.exit(0)
    
    # Discovery mode
    if args.discover:
        paracord.discover_servers()
        sys.exit(0)
    
    # Batch mode
    if args.config:
        paracord.run_batch(args.config, dry_run=args.dry_run, resume=args.resume,
                           skip_confirm=args.yes, meow_mode=args.meow,
                           skip_meowed=args.skip_meowed)
        sys.exit(0)
    
    # No action specified
    parser.print_help()


if __name__ == '__main__':
    main()
