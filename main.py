import discord
import json
import random
import re
import os
from discord import app_commands
from keep_alive import keep_alive



TOKEN = os.getenv("DISCORD_BOT_TOKEN")

GUILD_ID = os.getenv("GUILD_ID")  # Get the guild ID from the environment
# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Data storage
pending_invites = {}  # Store pending invites: {player_id: team_name}
teams = {}  # {team_name: {"player1": user_id, "player2": user_id or None, "lol_name1": str, "lol_name2": str or None}}

DATA_FILE = 'Arena.txt'

def save_data():
    """Save teams and pending invites data to Arena.txt"""
    data = {
        "teams": teams,
        "pending_invites": pending_invites
    }
    with open(DATA_FILE, 'w') as file:
        json.dump(data, file, indent=4)

def load_data():
    """Load data from Arena.txt"""
    global teams, pending_invites
    try:
        with open(DATA_FILE, 'r') as file:
            data = json.load(file)
            teams = data.get("teams", {})
            pending_invites = data.get("pending_invites", {})
    except (FileNotFoundError, json.JSONDecodeError):
        save_data()  # If file doesn't exist or is corrupted, create a fresh one


@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)

    print("Syncing commands...")
    await tree.sync(guild=guild)  # Normal sync
    await tree.sync()  # Global sync (if needed)
    print("Commands synced!")

    load_data()  # Load data when bot starts
    print(f'Logged in as {bot.user}')


async def create_team_role(guild, team_name, members):
    role = discord.utils.get(guild.roles, name=team_name)
    if not role:
        role = await guild.create_role(name="Team: " + team_name, color=discord.Color.random(), hoist=True, reason="Tournament team role")
    for member in members:
        await member.add_roles(role)
    return role

async def create_team_category_and_channels(guild, team_name, members):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    for member in members:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True)

    category = await guild.create_category(team_name, overwrites=overwrites)
    await guild.create_text_channel(f"{team_name}-chat", category=category)
    await guild.create_voice_channel(f"{team_name}-voice", category=category)
    return category

async def teamvalid(team_name, user, create, interaction):
    """Check if a team can be created or joined"""
    if not create:
        if team_name not in teams:
            await interaction.response.send_message("This team doesn't exist! ❌", ephemeral=True)
            return False
        if teams[team_name]["player2"] is not None:
            await interaction.response.send_message("This team is already full! ❌", ephemeral=True)
            return False
        for team in teams.values():
            if user.id in (team["player1"], team["player2"]):
                await interaction.response.send_message("You're already in a team! ❌", ephemeral=True)
                return False

    if create:
        if team_name in teams:
            await interaction.response.send_message("This team name is already taken! ❌", ephemeral=True)
            return False
        for team in teams.values():
            if user.id in (team["player1"], team["player2"]):
                await interaction.response.send_message("You're already in a team! ❌", ephemeral=True)
                return False

    return True

####################### Create team #######################################################
@tree.command(name="create_team", description="Create a new team")
@app_commands.describe(team_name="Your team name", lol_name="Your League of Legends name (Summoner#Tag)")
async def create_team(interaction: discord.Interaction, team_name: str, lol_name: str):
    user = interaction.user
    guild = interaction.guild

    # Validate LoL name format
    if not re.match(r"^[a-zA-Z0-9]+#[0-9]{3,5}$", lol_name):
        await interaction.response.send_message("Invalid League of Legends name format! ❌\nUse: `Summoner#Tag` (e.g., `larrastiar#666`)", ephemeral=True)
        return

    if not await teamvalid(team_name, user, True, interaction):
        return

    teams[team_name] = {"player1": user.id, "player2": None, "lol_name1": lol_name, "lol_name2": None}

    save_data()

    await interaction.response.send_message(
        f"{user.mention} created team **{team_name}**! 🎉\n"
        f"League of Legends Name: `{lol_name}`\n"
        f"Waiting for a second player to join..."
    )

####################### Invite player ###################################################
async def send_invite(player_to_invite, team_name, interaction):
            try:
                # Try sending a test message to check if DMs are enabled
                dm_channel = await player_to_invite.create_dm()
                invite_message = f"Hello {player_to_invite.mention}, you've been invited to join **{team_name}**! Please respond with your League of Legends summoner             name in the format `Summoner#Tag` (e.g., `Summoner#1234`)."
                await dm_channel.send(invite_message)

                # Store the invite details temporarily
                pending_invites[player_to_invite.id] = {
                    "team_name": team_name,
                    "guild": interaction.guild,
                    "player1": interaction.user
                }

                # Send the confirmation message only here
                await interaction.response.send_message(f"{player_to_invite.mention} has been invited to join your team!")
            except discord.errors.Forbidden:
                # If the bot can't DM the user, notify them in the server
                await interaction.response.send_message(
                    f"Sorry, {player_to_invite.mention}, I can't DM you. Please enable DMs for this server to receive team invitations.",
                    ephemeral=True
                )


@tree.command(name="invite_player", description="Invite a player to your team")
@app_commands.describe(player_name="The player you want to invite")
async def invite_player(interaction: discord.Interaction, player_name: str):
    user = interaction.user
    guild = interaction.guild

    # Check if the user is already in a team
    for team_name, team_info in teams.items():
        if team_info['player1'] == user.id and team_info['player2'] is None:
            # Player is the first team member and there's an open spot
            player_to_invite = discord.utils.get(guild.members, name=player_name)
            if player_to_invite:
                # Send an invite to the player
                await send_invite(player_to_invite, team_name, interaction)
            else:
                await interaction.response.send_message("Player not found!", ephemeral=True)
            return
    await interaction.response.send_message("You need to create a team first!", ephemeral=True)


####################### Handle user accepting invite ###################################################
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Check if the message is from a user with a pending invite
    if message.author.id in pending_invites:
        invite_data = pending_invites.pop(message.author.id)
        team_name = invite_data["team_name"]
        guild = invite_data["guild"]
        player1 = invite_data["player1"]

        # Validate the Summoner#Tag format
        lol_name = message.content.strip()
        if not re.match(r"^[a-zA-Z0-9]+#[0-9]{3,5}$", lol_name):
            await message.channel.send("Invalid League of Legends name format! Please use `Summoner#Tag` (e.g., `Summoner#1234`).")
            return

        # Now we have both players' Summoner#Tag and can create roles
        player2 = message.author
        summoner_name1 = invite_data["player1"].name  # Player 1's Summoner#Tag
        summoner_name2 = lol_name  # Player 2's Summoner#Tag

        # Create a unique color for the team
        color = discord.Color.random()

        # Create 3 roles: one for the team, and one for each player's Summoner#Tag
        role_team = await guild.create_role(name=f"Team: {team_name}", color=color, hoist=True, reason="Tournament team role")

        # Get the Member objects for both players
        member1 = guild.get_member(player1.id)
        member2 = guild.get_member(player2.id)

        # Check if we have valid Member objects for both players
        if not member1:
            await message.channel.send(f"{player1.name} is not a member of the guild. Cannot assign a role.")
            return
        if not member2:
            await message.channel.send(f"{player2.name} is not a member of the guild. Cannot assign a role.")
            return

        # Create the roles for the summoner names
        role1 = await guild.create_role(name=f"{teams[team_name]['lol_name1']}", color=color, hoist=True, reason="Player 1 Summoner role")
        role2 = await guild.create_role(name=f"{summoner_name2}", color=color, hoist=True, reason="Player 2 Summoner role")

        # Assign roles to the players
        await member1.add_roles(role_team, role1)
        await member2.add_roles(role_team, role2)

        # Create a private category and text/voice channels for the team
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),  # Everyone can't see the category
            member1: discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True),  # Player 1 has access
            member2: discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True),  # Player 2 has access
        }

        # Create the private category
        category = await guild.create_category(team_name, overwrites=overwrites)

        # Create private text and voice channels within the category
        await guild.create_text_channel(f"{team_name}-chat", category=category)
        await guild.create_voice_channel(f"{team_name}-voice", category=category)

        # Save the team data to local storage
        teams[team_name]["player2"] = player2.id
        teams[team_name]["lol_name2"] = lol_name
        save_data()

        # Confirm team creation and roles assignment
        await message.channel.send(f"Team **{team_name}** has been created successfully! 🎉\n"
                                   f"{member1.mention} and {member2.mention} are now on the team with roles `{summoner_name1}` and `{summoner_name2}`.")


keep_alive()

bot.run(TOKEN)
